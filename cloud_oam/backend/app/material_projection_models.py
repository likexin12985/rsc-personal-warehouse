"""Reviewed material publications and immutable per-SKU version lineage."""
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .foundation_models import CreatedAtMixin, JSON_DOCUMENT, UUID_TYPE, uuid4_value


class MaterialProjectionPublication(CreatedAtMixin, Base):
    __tablename__ = 'material_projection_publications'
    __table_args__ = (
        UniqueConstraint('actor_user_id', 'idempotency_key', name='uq_material_publication_key'),
        UniqueConstraint('actor_user_id', 'request_id', name='uq_material_publication_request'),
        CheckConstraint('record_count BETWEEN 1 AND 1000 AND actor_authorization_version>0', name='ck_material_publication_count'),
        CheckConstraint('length(payload_sha256)=64 AND length(request_sha256)=64 AND length(review_sha256)=64 AND length(evidence_sha256)=64', name='ck_material_publication_hashes'),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    receipt_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('oam_material_capture_receipts.id', ondelete='RESTRICT'), unique=True)
    binding_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('oam_material_capture_bindings.id', ondelete='RESTRICT'))
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey('users.id', ondelete='RESTRICT'))
    actor_person_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('people.id', ondelete='RESTRICT'))
    actor_authorization_version: Mapped[int] = mapped_column(BigInteger)
    auth_session_id: Mapped[str] = mapped_column(String(36), ForeignKey('auth_sessions.id', ondelete='RESTRICT'))
    evidence_file_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('files.id', ondelete='RESTRICT'))
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    record_count: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(160))
    request_sha256: Mapped[str] = mapped_column(String(64))
    review_sha256: Mapped[str] = mapped_column(String(64))
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    audit_event_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('audit_events.id', ondelete='RESTRICT'), unique=True)


class MaterialProjectionLine(Base):
    __tablename__ = 'material_projection_lines'
    __table_args__ = (
        UniqueConstraint('publication_id', 'sequence', name='uq_material_projection_sequence'),
        UniqueConstraint('publication_id', 'sku_code', name='uq_material_projection_sku'),
        CheckConstraint('sequence BETWEEN 1 AND 1000 AND length(payload_sha256)=64', name='ck_material_projection_line'),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    publication_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('material_projection_publications.id', ondelete='RESTRICT'))
    sequence: Mapped[int] = mapped_column(Integer)
    sku_code: Mapped[str] = mapped_column(String(80))
    material_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('materials.id', ondelete='RESTRICT'), index=True)
    external_object_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('external_objects.id', ondelete='RESTRICT'))
    version_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('external_object_versions.id', ondelete='RESTRICT'), unique=True)
    policy_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('material_inventory_policies.id', ondelete='RESTRICT'))
    previous_line_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, ForeignKey('material_projection_lines.id', ondelete='RESTRICT'), nullable=True, unique=True)
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_sha256: Mapped[str] = mapped_column(String(64))
