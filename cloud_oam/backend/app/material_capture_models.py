"""Transport registration and immutable material capture staging, not projections."""
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .foundation_models import CreatedAtMixin, JSON_DOCUMENT, UUID_TYPE, uuid4_value


class MaterialCaptureBinding(CreatedAtMixin, Base):
    __tablename__ = 'oam_material_capture_bindings'
    __table_args__ = (
        UniqueConstraint('source_instance', 'key_id', name='uq_material_capture_source_key'),
        CheckConstraint('valid_to > valid_from AND valid_from >= created_at', name='ck_material_capture_binding_window'),
        CheckConstraint('revoked_at IS NULL OR revoked_at >= created_at', name='ck_material_capture_binding_revocation'),
        CheckConstraint('length(key_fingerprint)=64', name='ck_material_capture_binding_hash'),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    source_system_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('source_systems.id', ondelete='RESTRICT'))
    source_instance: Mapped[str] = mapped_column(String(128))
    key_id: Mapped[str] = mapped_column(String(128))
    key_fingerprint: Mapped[str] = mapped_column(String(64))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MaterialCaptureReceipt(CreatedAtMixin, Base):
    __tablename__ = 'oam_material_capture_receipts'
    __table_args__ = (
        UniqueConstraint('source_instance', 'capture_id', name='uq_material_capture_receipt'),
        UniqueConstraint('source_instance', 'request_id', name='uq_material_capture_request'),
        CheckConstraint('observed_count BETWEEN 0 AND 100000', name='ck_material_capture_count'),
        CheckConstraint('capture_started_at <= capture_completed_at AND capture_completed_at <= created_at', name='ck_material_capture_interval'),
        CheckConstraint('length(key_fingerprint)=64 AND length(capture_sha256)=64 AND length(body_sha256)=64 AND length(records_sha256)=64', name='ck_material_capture_receipt_hashes'),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    binding_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('oam_material_capture_bindings.id', ondelete='RESTRICT'))
    source_instance: Mapped[str] = mapped_column(String(128))
    capture_id: Mapped[UUID] = mapped_column(UUID_TYPE)
    request_id: Mapped[str] = mapped_column(String(128))
    key_id: Mapped[str] = mapped_column(String(128))
    key_fingerprint: Mapped[str] = mapped_column(String(64))
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capture_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capture_completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_count: Mapped[int] = mapped_column(Integer)
    records_sha256: Mapped[str] = mapped_column(String(64))
    capture_sha256: Mapped[str] = mapped_column(String(64))
    body_sha256: Mapped[str] = mapped_column(String(64))
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
