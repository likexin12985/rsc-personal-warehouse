"""Immutable negative execution facts for original daily review requests."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import BigInteger,CheckConstraint,DateTime,ForeignKey,String,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from ..database import Base
from ..foundation_models import UUID_TYPE,JSON_DOCUMENT

class DailyReviewRequestSeal(Base):
    __tablename__='daily_review_request_seals'
    __table_args__=(
        UniqueConstraint('actor_user_id','request_id',name='uq_daily_review_seal_request'),
        CheckConstraint('original_authorization_version>0 AND original_review_version>=0','ck_daily_review_seal_versions'),
        CheckConstraint("operation IN ('open','explain','approve','request_changes')",'ck_daily_review_seal_operation'),
        CheckConstraint('length(request_id) BETWEEN 8 AND 160','ck_daily_review_seal_request'),
        CheckConstraint('access_issued_at>0 AND access_expires_at>access_issued_at','ck_daily_review_seal_token'),
    )
    id:Mapped[UUID]=mapped_column(UUID_TYPE,primary_key=True)
    cutoff_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('daily_reconciliation_cutoffs.id'))
    actor_user_id:Mapped[str]=mapped_column(String(36),ForeignKey('users.id'))
    actor_person_id:Mapped[UUID]=mapped_column(UUID_TYPE,ForeignKey('people.id'))
    original_authorization_version:Mapped[int]=mapped_column(BigInteger)
    original_review_version:Mapped[int]=mapped_column(BigInteger)
    operation:Mapped[str]=mapped_column(String(24))
    request_id:Mapped[str]=mapped_column(String(160))
    actor_snapshot:Mapped[dict]=mapped_column(JSON_DOCUMENT)
    auth_session_id:Mapped[str]=mapped_column(String(36),ForeignKey('auth_sessions.id'))
    access_issued_at:Mapped[int]=mapped_column(BigInteger)
    access_expires_at:Mapped[int]=mapped_column(BigInteger)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    transaction_id:Mapped[int]=mapped_column(BigInteger)
