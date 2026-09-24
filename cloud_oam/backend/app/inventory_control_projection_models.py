"""Immutable control publication, normalized lines, origins and version closure."""
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .foundation_models import CreatedAtMixin, JSON_DOCUMENT, UUID_TYPE, uuid4_value


class ControlProjectionPublication(CreatedAtMixin, Base):
    __tablename__ = 'control_projection_publications'
    __table_args__ = (
        UniqueConstraint('actor_user_id', 'idempotency_key', name='uq_control_publication_key'),
        UniqueConstraint('actor_user_id', 'request_id', name='uq_control_publication_request'),
        UniqueConstraint('source_system_id', 'region_org_id', 'captured_at', name='uq_control_publication_capture'),
        Index('uq_control_publication_first', 'source_system_id', 'region_org_id', unique=True,
              postgresql_where=text('previous_publication_id IS NULL'), sqlite_where=text('previous_publication_id IS NULL')),
        CheckConstraint('record_count>=0 AND origin_count>=record_count AND actor_authorization_version>0', name='ck_control_publication_count'),
        CheckConstraint('captured_at<=created_at AND created_at<valid_until', name='ck_control_publication_time'),
        CheckConstraint('length(payload_sha256)=64 AND length(request_sha256)=64 AND length(review_sha256)=64 AND length(evidence_sha256)=64', name='ck_control_publication_hashes'),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    preparation_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('inventory_control_preparations.id', ondelete='RESTRICT'), unique=True)
    source_system_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('source_systems.id', ondelete='RESTRICT'))
    region_org_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('organizations.id', ondelete='RESTRICT'))
    mapping_decision_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('inventory_control_mapping_decisions.id', ondelete='RESTRICT'))
    previous_publication_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, ForeignKey('control_projection_publications.id', ondelete='RESTRICT'), nullable=True, unique=True)
    sync_run_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('sync_runs.id', ondelete='RESTRICT'), unique=True)
    sync_batch_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('sync_batches.id', ondelete='RESTRICT'), unique=True)
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey('users.id', ondelete='RESTRICT'))
    actor_person_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('people.id', ondelete='RESTRICT'))
    actor_authorization_version: Mapped[int] = mapped_column(BigInteger)
    auth_session_id: Mapped[str] = mapped_column(String(36), ForeignKey('auth_sessions.id', ondelete='RESTRICT'))
    evidence_file_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('files.id', ondelete='RESTRICT'))
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    record_count: Mapped[int] = mapped_column(Integer)
    origin_count: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(160))
    request_sha256: Mapped[str] = mapped_column(String(64))
    review_sha256: Mapped[str] = mapped_column(String(64))
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    audit_event_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('audit_events.id', ondelete='RESTRICT'), unique=True)


class ControlProjectionLine(Base):
    __tablename__ = 'control_projection_lines'
    __table_args__ = (
        UniqueConstraint('publication_id', 'sequence', name='uq_control_line_sequence'),
        UniqueConstraint('publication_id', 'external_business_key', name='uq_control_line_business_key'),
        UniqueConstraint('id', 'publication_id', name='uq_control_line_publication'),
        CheckConstraint('sequence>0 AND control_qty>=0 AND length(payload_sha256)=64', name='ck_control_line_value'),
        CheckConstraint("condition_code IN ('new','used','damaged','scrapped')", name='ck_control_line_condition'),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    publication_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('control_projection_publications.id', ondelete='RESTRICT'))
    sequence: Mapped[int] = mapped_column(Integer)
    external_business_key: Mapped[str] = mapped_column(String(250))
    external_object_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('external_objects.id', ondelete='RESTRICT'), index=True)
    version_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('external_object_versions.id', ondelete='RESTRICT'), unique=True)
    sync_inbox_event_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('sync_inbox_events.id', ondelete='RESTRICT'), unique=True)
    previous_line_id: Mapped[UUID | None] = mapped_column(UUID_TYPE, ForeignKey('control_projection_lines.id', ondelete='RESTRICT'), nullable=True, unique=True)
    material_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('materials.id', ondelete='RESTRICT'))
    condition_code: Mapped[str] = mapped_column(String(24))
    control_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_sha256: Mapped[str] = mapped_column(String(64))


class ControlProjectionOrigin(Base):
    __tablename__ = 'control_projection_origins'
    __table_args__ = (
        ForeignKeyConstraint(['line_id', 'publication_id'], ['control_projection_lines.id', 'control_projection_lines.publication_id'], ondelete='RESTRICT'),
        UniqueConstraint('publication_id', 'external_business_key', name='uq_control_origin_key'),
        UniqueConstraint('line_id', 'sequence', name='uq_control_origin_sequence'),
        CheckConstraint('sequence>0 AND length(payload_sha256)=64', name='ck_control_origin_value'),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    publication_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('control_projection_publications.id', ondelete='RESTRICT'))
    line_id: Mapped[UUID] = mapped_column(UUID_TYPE)
    sequence: Mapped[int] = mapped_column(Integer)
    external_business_key: Mapped[str] = mapped_column(String(200))
    capture_snapshot_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('inventory_control_capture_snapshots.id', ondelete='RESTRICT'))
    staging_record_id: Mapped[str] = mapped_column(String(36), ForeignKey('external_sync_snapshot_records.id', ondelete='RESTRICT'))
    material_line_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('material_projection_lines.id', ondelete='RESTRICT'))
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_sha256: Mapped[str] = mapped_column(String(64))


class ControlProjectionClosure(Base):
    __tablename__ = 'control_projection_closures'
    __table_args__ = (CheckConstraint("reason IN ('replaced','absent') AND length(payload_sha256)=64", name='ck_control_closure_value'),)
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    publication_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('control_projection_publications.id', ondelete='RESTRICT'), index=True)
    closed_line_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('control_projection_lines.id', ondelete='RESTRICT'), unique=True)
    reason: Mapped[str] = mapped_column(String(24))
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_sha256: Mapped[str] = mapped_column(String(64))


TABLES = ('control_projection_publications', 'control_projection_lines', 'control_projection_origins', 'control_projection_closures')
