"""Unshipped return-stop witness; candidate until native migration validation."""
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.foundation_models import CreatedAtMixin, UUID_TYPE, uuid4_value


class StockLossReturnStop(CreatedAtMixin, Base):
    """The stop and its exact stock inverse must commit in one transaction.

    Actor, request, reason, quantity and posting are owned by the inverse fact.
    This witness does not cancel, delete or rewrite the original return order.
    """
    __tablename__ = 'stock_loss_return_stops'
    __table_args__ = (
        UniqueConstraint('return_operation_id', name='uq_loss_return_stop_operation'),
        UniqueConstraint('return_line_id', name='uq_loss_return_stop_line'),
        UniqueConstraint('reversal_id', name='uq_loss_return_stop_inverse'),
        UniqueConstraint('root_disposition_id', name='uq_loss_return_stop_root'),
        ForeignKeyConstraint(['reversal_id', 'root_disposition_id'],
            ['stock_loss_disposition_reversals.id', 'stock_loss_disposition_reversals.root_disposition_id'],
            name='fk_loss_return_stop_inverse_root', ondelete='RESTRICT',
            deferrable=True, initially='DEFERRED'),
        CheckConstraint('length(evidence_fingerprint)=64', name='ck_loss_return_stop_fingerprint'),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    root_disposition_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('stock_loss_dispositions.id', ondelete='RESTRICT'))
    reversal_id: Mapped[UUID] = mapped_column(UUID_TYPE)
    return_operation_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('stock_operation_orders.id', ondelete='RESTRICT'))
    return_line_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('stock_operation_lines.id', ondelete='RESTRICT'))
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
