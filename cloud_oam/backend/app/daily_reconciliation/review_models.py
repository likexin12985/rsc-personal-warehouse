"""Daily review facts and rebuildable binding; quantities stay in generic tables."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import BigInteger,CheckConstraint,DateTime,ForeignKey,String,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from ..database import Base
from ..foundation_models import UUID_TYPE,JSON_DOCUMENT

class DailyReviewEvent(Base):
    __tablename__='daily_review_events'
    __table_args__=(
        CheckConstraint('version>0','ck_daily_review_event_version'),
        CheckConstraint('length(payload_sha256)=64','ck_daily_review_event_hash'),
        CheckConstraint('access_issued_at>0 AND access_expires_at>access_issued_at','ck_daily_review_event_token_time'),
        UniqueConstraint('run_id','version',name='uq_daily_review_event_version'),
        UniqueConstraint('actor_user_id','idempotency_key',name='uq_daily_review_actor_key'),
        UniqueConstraint('actor_user_id','request_id',name='uq_daily_review_actor_request'),
    )
    id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('daily_review_consumptions.event_id',name='fk_daily_review_event_consumed',deferrable=True,initially='DEFERRED',use_alter=True),primary_key=True)
    run_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('daily_review_bindings.run_id',name='fk_daily_review_event_run',deferrable=True,initially='DEFERRED',use_alter=True))
    cutoff_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('daily_reconciliation_cutoffs.id'))
    version:Mapped[int]=mapped_column(BigInteger)
    actor_user_id:Mapped[str]=mapped_column(String(36),ForeignKey('users.id'))
    actor_person_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('people.id'))
    auth_session_id:Mapped[str]=mapped_column(String(36),ForeignKey('auth_sessions.id'))
    access_issued_at:Mapped[int]=mapped_column(BigInteger)
    access_expires_at:Mapped[int]=mapped_column(BigInteger)
    idempotency_key:Mapped[str]=mapped_column(String(128))
    request_id:Mapped[str]=mapped_column(String(160))
    payload_jsonb:Mapped[dict]=mapped_column(JSON_DOCUMENT)
    after_state_jsonb:Mapped[dict]=mapped_column(JSON_DOCUMENT)
    receipt_jsonb:Mapped[dict]=mapped_column(JSON_DOCUMENT)
    payload_sha256:Mapped[str]=mapped_column(String(64))
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    transaction_id:Mapped[int]=mapped_column(BigInteger)

class DailyReviewBinding(Base):
    __tablename__='daily_review_bindings'
    __table_args__=(CheckConstraint('version>0','ck_daily_review_binding_version'),)
    run_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('reconciliation_runs.id',deferrable=True,initially='DEFERRED'),primary_key=True)
    cutoff_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('daily_reconciliation_cutoffs.id'),unique=True)
    version:Mapped[int]=mapped_column(BigInteger)
    last_event_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('daily_review_events.id'),unique=True)
    item_ids:Mapped[list]=mapped_column(JSON_DOCUMENT)
    state_jsonb:Mapped[dict]=mapped_column(JSON_DOCUMENT)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))

class DailyReviewConsumption(Base):
    __tablename__='daily_review_consumptions'
    event_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('daily_review_events.id'),primary_key=True)
    audit_event_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('audit_events.id'),unique=True)
    transition_event_id:Mapped[UUID|None]=mapped_column(UUID_TYPE,ForeignKey('state_transition_events.id'),unique=True,nullable=True)
    transaction_id:Mapped[int]=mapped_column(BigInteger)
