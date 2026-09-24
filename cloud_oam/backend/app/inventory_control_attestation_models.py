"""Authenticated collector receipts; no inventory or publication authority."""
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .foundation_models import CreatedAtMixin, JSON_DOCUMENT, UUID_TYPE, uuid4_value


class InventoryControlCaptureAttestation(CreatedAtMixin, Base):
    __tablename__ = 'inventory_control_capture_attestations'
    __table_args__ = (
        UniqueConstraint('snapshot_ref_id', name='uq_control_attestation_snapshot'),
        UniqueConstraint('source_instance', 'request_id', name='uq_control_attestation_request'),
        CheckConstraint('length(key_fingerprint)=64 AND length(payload_sha256)=64 AND length(body_sha256)=64', name='ck_control_attestation_hashes'),
        CheckConstraint('length(key_id) BETWEEN 1 AND 128 AND length(source_instance) BETWEEN 1 AND 128 AND length(request_id) BETWEEN 1 AND 128', name='ck_control_attestation_identity'),
        CheckConstraint("entity_type='inventory'", name='ck_control_attestation_entity'),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    snapshot_ref_id: Mapped[str] = mapped_column(String(36), ForeignKey('external_sync_snapshots.id', ondelete='RESTRICT'))
    source_instance: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(32), default='inventory')
    request_id: Mapped[str] = mapped_column(String(128))
    key_id: Mapped[str] = mapped_column(String(128))
    key_fingerprint: Mapped[str] = mapped_column(String(64))
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    body_sha256: Mapped[str] = mapped_column(String(64))
