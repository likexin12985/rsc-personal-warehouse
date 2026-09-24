"""Immutable approval/revocation of exact control normalization rule versions."""
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .foundation_models import CreatedAtMixin, JSON_DOCUMENT, UUID_TYPE, uuid4_value


class InventoryControlMappingDecision(CreatedAtMixin, Base):
    __tablename__ = 'inventory_control_mapping_decisions'
    __table_args__ = (
        CheckConstraint("action IN ('grant','revoke')", name='ck_control_mapping_action'),
        CheckConstraint('valid_from>=created_at AND (valid_to IS NULL OR valid_to>valid_from)', name='ck_control_mapping_validity'),
        CheckConstraint("(action='grant' AND revoked_grant_id IS NULL) OR (action='revoke' AND revoked_grant_id IS NOT NULL AND valid_to IS NULL AND valid_from=created_at)", name='ck_control_mapping_shape'),
        CheckConstraint('actor_authorization_version>0 AND length(rules_sha256)=64 AND length(payload_sha256)=64 AND length(request_sha256)=64 AND length(review_sha256)=64 AND length(evidence_sha256)=64', name='ck_control_mapping_proof'),
        UniqueConstraint('actor_user_id','idempotency_key',name='uq_control_mapping_actor_key'),
        UniqueConstraint('actor_user_id','request_id',name='uq_control_mapping_actor_request'),
        UniqueConstraint('revoked_grant_id',name='uq_control_mapping_revocation'),
        Index('uq_control_mapping_revision','binding_id','catalog_id','rules_revision',unique=True,
              postgresql_where=text("action='grant'"),sqlite_where=text("action='grant'")),
        Index('ix_control_mapping_subject','binding_id','catalog_id','created_at','id'),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE,primary_key=True,default=uuid4_value)
    binding_id: Mapped[UUID] = mapped_column(UUID_TYPE,ForeignKey('inventory_control_source_bindings.id',ondelete='RESTRICT'))
    catalog_id: Mapped[UUID] = mapped_column(UUID_TYPE,ForeignKey('inventory_control_catalog_versions.id',ondelete='RESTRICT'))
    action: Mapped[str] = mapped_column(String(24))
    revoked_grant_id: Mapped[UUID | None] = mapped_column(UUID_TYPE,ForeignKey('inventory_control_mapping_decisions.id',ondelete='RESTRICT'),nullable=True)
    rules_revision: Mapped[str] = mapped_column(String(80))
    rules_jsonb: Mapped[dict[str,Any]] = mapped_column(JSON_DOCUMENT)
    rules_sha256: Mapped[str] = mapped_column(String(64))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),nullable=True)
    actor_user_id: Mapped[str] = mapped_column(String(36),ForeignKey('users.id',ondelete='RESTRICT'))
    actor_person_id: Mapped[UUID] = mapped_column(UUID_TYPE,ForeignKey('people.id',ondelete='RESTRICT'))
    actor_authorization_version: Mapped[int] = mapped_column(BigInteger)
    auth_session_id: Mapped[str] = mapped_column(String(36),ForeignKey('auth_sessions.id',ondelete='RESTRICT'))
    evidence_file_id: Mapped[UUID] = mapped_column(UUID_TYPE,ForeignKey('files.id',ondelete='RESTRICT'))
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(160))
    request_sha256: Mapped[str] = mapped_column(String(64))
    review_sha256: Mapped[str] = mapped_column(String(64))
    payload_jsonb: Mapped[dict[str,Any]] = mapped_column(JSON_DOCUMENT)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    audit_event_id: Mapped[UUID] = mapped_column(UUID_TYPE,ForeignKey('audit_events.id',ondelete='RESTRICT'),unique=True)
