"""Immutable capture receipt; queried only through the narrow 0131 view."""
from sqlalchemy import BigInteger,CheckConstraint,Date,DateTime,ForeignKey,String,UniqueConstraint,Index
from sqlalchemy.orm import Mapped,mapped_column
from datetime import date,datetime
from uuid import UUID
from app.database import Base
from app.foundation_models import UUID_TYPE,JSON_DOCUMENT

class DailyCutoff(Base):
 __tablename__='daily_reconciliation_cutoffs'
 __table_args__=(Index('ix_daily_cutoff_region_id','region_org_id','id'),UniqueConstraint('actor_user_id','idempotency_key',name='uq_daily_cutoff_actor_key'),
  UniqueConstraint('actor_user_id','request_id',name='uq_daily_cutoff_actor_request'),
  UniqueConstraint('source_publication_id','mapping_decision_id','local_ledger_cursor',name='uq_daily_cutoff_basis'),
  CheckConstraint('local_ledger_cursor>=0 AND actor_authorization_version>0','ck_daily_cutoff_versions'),
  CheckConstraint('source_captured_at<=local_captured_at AND local_captured_at<=created_at','ck_daily_cutoff_time'),
  CheckConstraint('length(payload_sha256)=64 AND length(request_sha256)=64','ck_daily_cutoff_hashes'))
 id:Mapped[UUID]=mapped_column(UUID_TYPE,primary_key=True)
 business_date:Mapped[date]=mapped_column(Date)
 source_publication_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('control_projection_publications.id',ondelete='RESTRICT'))
 mapping_decision_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('daily_comparison_mapping_decisions.id',ondelete='RESTRICT'))
 source_system_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('source_systems.id',ondelete='RESTRICT'))
 region_org_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('organizations.id',ondelete='RESTRICT'))
 local_ledger_cursor:Mapped[int]=mapped_column(BigInteger)
 source_captured_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
 local_captured_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
 created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
 actor_user_id:Mapped[str]=mapped_column(String(36),ForeignKey('users.id',ondelete='RESTRICT'))
 actor_person_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('people.id',ondelete='RESTRICT'))
 actor_authorization_version:Mapped[int]=mapped_column(BigInteger)
 auth_session_id:Mapped[str]=mapped_column(String(36),ForeignKey('auth_sessions.id',ondelete='RESTRICT'))
 idempotency_key:Mapped[str]=mapped_column(String(128))
 request_id:Mapped[str]=mapped_column(String(160))
 request_sha256:Mapped[str]=mapped_column(String(64))
 payload_jsonb:Mapped[dict]=mapped_column(JSON_DOCUMENT)
 payload_sha256:Mapped[str]=mapped_column(String(64))
 audit_event_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('audit_events.id',ondelete='RESTRICT'),unique=True)
