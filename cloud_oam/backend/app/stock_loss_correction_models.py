"""Declarative loss correction facts, independent of application services."""
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint, Index,
    Numeric, String, Table, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.foundation_models import CreatedAtMixin, JSON_DOCUMENT, UUID_TYPE, uuid4_value


KINDS = "('restore_available','convert_used','convert_damaged','return_to_region','scrap')"


class RequestContext:
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    root_disposition_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('stock_loss_dispositions.id', ondelete='RESTRICT'))
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey('users.id', ondelete='RESTRICT'))
    actor_person_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey('people.id', ondelete='RESTRICT'))
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    request_id: Mapped[str] = mapped_column(String(160))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    command_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)


def request_constraints(prefix):
    return (
        UniqueConstraint('actor_user_id', 'request_id', name='uq_' + prefix + '_request'),
        UniqueConstraint('idempotency_key_hash', name='uq_' + prefix + '_key'),
        UniqueConstraint('id', 'root_disposition_id', name='uq_' + prefix + '_root'),
        CheckConstraint('authorization_version > 0 AND length(reason) BETWEEN 1 AND 500',
            name='ck_' + prefix + '_context'),
        CheckConstraint('length(request_id) BETWEEN 8 AND 160 AND length(idempotency_key_hash)=64 AND length(request_hash)=64',
            name='ck_' + prefix + '_request'),
    )


class StockLossDispositionReversal(RequestContext, CreatedAtMixin, Base):
    __tablename__ = 'stock_loss_disposition_reversals'
    __table_args__ = (
        *request_constraints('loss_inverse'),
        # NULL means the original root; otherwise reverse one precise successor
        # in the same root chain. No untyped "original object id" is accepted.
        ForeignKeyConstraint(['reversed_correction_id', 'root_disposition_id'],
            ['stock_loss_correction_executions.id', 'stock_loss_correction_executions.root_disposition_id'],
            name='fk_loss_inverse_correction_root', ondelete='RESTRICT',
            deferrable=True, initially='DEFERRED', use_alter=True),
        Index('uq_loss_inverse_original_root', 'root_disposition_id', unique=True,
            postgresql_where=text('reversed_correction_id IS NULL'),
            sqlite_where=text('reversed_correction_id IS NULL')),
        UniqueConstraint('reversed_correction_id', name='uq_loss_inverse_correction'),
        UniqueConstraint('original_transaction_id', name='uq_loss_inverse_original_tx'),
        UniqueConstraint('original_movement_id', name='uq_loss_inverse_original_move'),
        UniqueConstraint('posting_transaction_id', name='uq_loss_inverse_posted_tx'),
        UniqueConstraint('posting_movement_id', name='uq_loss_inverse_posted_move'),
        CheckConstraint('quantity > 0 AND original_transaction_id <> posting_transaction_id AND original_movement_id <> posting_movement_id',
            name='ck_loss_inverse_posting'),
        CheckConstraint('source_account_id IS NULL OR source_account_id <> target_account_id',
            name='ck_loss_inverse_accounts'),
        CheckConstraint('length(plan_hash)=64', name='ck_loss_inverse_plan'),
    )
    reversed_correction_id: Mapped[UUID | None] = mapped_column(UUID_TYPE)
    original_transaction_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('inventory_transactions.id', ondelete='RESTRICT'))
    original_movement_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('inventory_movements.id', ondelete='RESTRICT'))
    posting_transaction_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('inventory_transactions.id', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'))
    posting_movement_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('inventory_movements.id', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'))
    # A scrapped source is an external boundary; full proof must establish that
    # exception. A nullable source is never sufficient authority to restore SN.
    source_account_id: Mapped[UUID | None] = mapped_column(UUID_TYPE,
        ForeignKey('stock_accounts.id', ondelete='RESTRICT'))
    target_account_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('stock_accounts.id', ondelete='RESTRICT'))
    custody_assignment_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('custody_assignments.id', ondelete='RESTRICT'))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    plan_hash: Mapped[str] = mapped_column(String(64))
    plan_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)


class StockLossCorrectionDecision(RequestContext, CreatedAtMixin, Base):
    __tablename__ = 'stock_loss_correction_decisions'
    __table_args__ = (
        *request_constraints('loss_correction_decision'),
        ForeignKeyConstraint(['reversal_id', 'root_disposition_id'],
            ['stock_loss_disposition_reversals.id', 'stock_loss_disposition_reversals.root_disposition_id'],
            name='fk_loss_correction_decision_inverse_root', ondelete='RESTRICT'),
        UniqueConstraint('id', 'root_disposition_id', 'reversal_id', 'disposition',
            name='uq_loss_correction_decision_binding'),
        CheckConstraint('disposition IN ' + KINDS, name='ck_loss_correction_decision_kind'),
        CheckConstraint('length(expected_reversal_hash)=64', name='ck_loss_correction_decision_hash'),
    )
    reversal_id: Mapped[UUID] = mapped_column(UUID_TYPE)
    expected_reversal_hash: Mapped[str] = mapped_column(String(64))
    disposition: Mapped[str] = mapped_column(String(24))


class StockLossCorrectionExecution(RequestContext, CreatedAtMixin, Base):
    __tablename__ = 'stock_loss_correction_executions'
    __table_args__ = (
        *request_constraints('loss_correction_execution'),
        # The actual kind is part of the FK. Reusing a valid decision while
        # silently changing its outcome fails independently of application code.
        ForeignKeyConstraint(['correction_decision_id', 'root_disposition_id', 'reversal_id', 'disposition'],
            ['stock_loss_correction_decisions.id', 'stock_loss_correction_decisions.root_disposition_id',
             'stock_loss_correction_decisions.reversal_id', 'stock_loss_correction_decisions.disposition'],
            name='fk_loss_correction_execution_decision', ondelete='RESTRICT'),
        UniqueConstraint('reversal_id', name='uq_loss_correction_execution_inverse'),
        UniqueConstraint('correction_decision_id', name='uq_loss_correction_execution_decision'),
        UniqueConstraint('posting_transaction_id', name='uq_loss_correction_execution_tx'),
        UniqueConstraint('posting_movement_id', name='uq_loss_correction_execution_move'),
        UniqueConstraint('return_operation_id', name='uq_loss_correction_execution_return'),
        CheckConstraint('disposition IN ' + KINDS, name='ck_loss_correction_execution_kind'),
        CheckConstraint("(disposition='scrap' AND target_account_id IS NULL) OR (disposition<>'scrap' AND target_account_id IS NOT NULL AND source_account_id<>target_account_id)",
            name='ck_loss_correction_execution_accounts'),
        CheckConstraint("(disposition='return_to_region' AND return_operation_id IS NOT NULL) OR (disposition<>'return_to_region' AND return_operation_id IS NULL)",
            name='ck_loss_correction_execution_return'),
        CheckConstraint('quantity > 0 AND length(plan_hash)=64', name='ck_loss_correction_execution_plan'),
    )
    correction_decision_id: Mapped[UUID] = mapped_column(UUID_TYPE)
    reversal_id: Mapped[UUID] = mapped_column(UUID_TYPE)
    disposition: Mapped[str] = mapped_column(String(24))
    return_operation_id: Mapped[UUID | None] = mapped_column(UUID_TYPE,
        ForeignKey('stock_operation_orders.id', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'))
    source_account_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('stock_accounts.id', ondelete='RESTRICT'))
    target_account_id: Mapped[UUID | None] = mapped_column(UUID_TYPE,
        ForeignKey('stock_accounts.id', ondelete='RESTRICT'))
    custody_assignment_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('custody_assignments.id', ondelete='RESTRICT'))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    posting_transaction_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('inventory_transactions.id', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'))
    posting_movement_id: Mapped[UUID] = mapped_column(UUID_TYPE,
        ForeignKey('inventory_movements.id', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'))
    plan_hash: Mapped[str] = mapped_column(String(64))
    plan_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)


TABLES = (StockLossDispositionReversal.__table__, StockLossCorrectionDecision.__table__,
          StockLossCorrectionExecution.__table__)


class StockLossInverseRequestSeal(RequestContext, CreatedAtMixin, Base):
    __tablename__ = 'stock_loss_inverse_request_seals'
    __table_args__ = (
        *request_constraints('loss_inverse_seal'),
        UniqueConstraint('reversal_key_hash', name='uq_loss_inverse_seal_reversal_key'),
        UniqueConstraint('approval_key_hash', name='uq_loss_inverse_seal_approval_key'),
        UniqueConstraint('correction_key_hash', name='uq_loss_inverse_seal_correction_key'),
        CheckConstraint('length(reversal_key_hash)=64 AND length(approval_key_hash)=64 AND length(correction_key_hash)=64 '
            'AND length(plan_hash)=64 AND length(request_reference)=82', name='ck_loss_inverse_seal_proof'),
        CheckConstraint('idempotency_key_hash=reversal_key_hash', name='ck_loss_inverse_seal_action'),
        CheckConstraint('reversal_key_hash<>approval_key_hash AND reversal_key_hash<>correction_key_hash '
            'AND approval_key_hash<>correction_key_hash', name='ck_loss_inverse_seal_distinct_keys'),
    )
    reversal_key_hash: Mapped[str] = mapped_column(String(64))
    approval_key_hash: Mapped[str] = mapped_column(String(64))
    correction_key_hash: Mapped[str] = mapped_column(String(64))
    request_reference: Mapped[str] = mapped_column(String(100))
    plan_hash: Mapped[str] = mapped_column(String(64))


# Schema metadata only: no ORM entity or public service writes this table.
# Production permissions allow registration solely through the controlled
# database function; the native migration owns those permissions and guards.
_binding_digests = ("request_hash", "key_token", "reversal_key_hash", "approval_key_hash", "correction_key_hash")
_binding_distinct = "reversal_key_hash<>approval_key_hash AND reversal_key_hash<>correction_key_hash AND approval_key_hash<>correction_key_hash"
stock_loss_request_key_bindings = Table(
    "stock_loss_request_key_bindings", Base.metadata,
    Column("fact_id", UUID_TYPE, primary_key=True),
    Column("binding_kind", String(24), nullable=False),
    Column("root_disposition_id", UUID_TYPE, ForeignKey("stock_loss_dispositions.id", ondelete="RESTRICT"), nullable=False),
    Column("actor_user_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    Column("request_id", String(160), nullable=False),
    *(Column(name, String(64), nullable=False) for name in _binding_digests),
    *(Column(name, UUID_TYPE) for name in ("inverse_id", "approval_id", "correction_id", "seal_id", "approval_seal_id", "correction_seal_id")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("actor_user_id", "request_id", name="uq_loss_request_binding_request"),
    *(UniqueConstraint(name, name="stock_loss_request_key_bindings_" + name + "_key")
      for name in _binding_digests if name != "request_hash"),
    *(ForeignKeyConstraint([column, "root_disposition_id"], [table + ".id", table + ".root_disposition_id"],
        name="fk_loss_request_binding_" + kind, ondelete="RESTRICT")
      for kind, column, table in (
          ("inverse", "inverse_id", "stock_loss_disposition_reversals"),
          ("approval", "approval_id", "stock_loss_correction_decisions"),
          ("correction", "correction_id", "stock_loss_correction_executions"),
          ("seal", "seal_id", "stock_loss_inverse_request_seals"),
          ("approval_seal", "approval_seal_id", "stock_loss_correction_approval_seals"),
          ("correction_seal", "correction_seal_id", "stock_loss_correction_execution_seals"),
      )),
    CheckConstraint(" OR ".join(
        "(binding_kind='" + kind + "' AND " + column + "=fact_id AND " + column + " IS NOT NULL AND "
        + " AND ".join(other + " IS NULL" for other in ("inverse_id", "approval_id", "correction_id", "seal_id", "approval_seal_id", "correction_seal_id") if other != column) + ")"
        for kind, column in (("inverse", "inverse_id"), ("approval", "approval_id"), ("correction", "correction_id"), ("inverse_seal", "seal_id"), ("approval_seal", "approval_seal_id"), ("correction_seal", "correction_seal_id"))
    ), name="ck_loss_request_binding_kind"),
    CheckConstraint(" AND ".join(name + " ~ '^[a-f0-9]{64}$'" for name in _binding_digests) + " AND " + _binding_distinct,
        name="ck_loss_request_binding_digests").ddl_if(dialect="postgresql"),
    CheckConstraint(" AND ".join("(length(" + name + ")=64 AND " + name + " NOT GLOB '*[^a-f0-9]*')" for name in _binding_digests)
        + " AND " + _binding_distinct, name="ck_loss_request_binding_digests").ddl_if(dialect="sqlite"),
    CheckConstraint("request_id ~ '^[A-Za-z0-9._:-]{8,160}$'", name="ck_loss_request_binding_request").ddl_if(dialect="postgresql"),
    CheckConstraint("length(request_id) BETWEEN 8 AND 160 AND request_id NOT GLOB '*[^A-Za-z0-9._:-]*'",
        name="ck_loss_request_binding_request").ddl_if(dialect="sqlite"),
)


class CorrectionSealKeys:
    reversal_key_hash: Mapped[str] = mapped_column(String(64))
    approval_key_hash: Mapped[str] = mapped_column(String(64))
    correction_key_hash: Mapped[str] = mapped_column(String(64))
    request_reference: Mapped[str] = mapped_column(String(100))
    reversal_id: Mapped[UUID] = mapped_column(UUID_TYPE)
    expected_reversal_hash: Mapped[str] = mapped_column(String(64))
    disposition: Mapped[str] = mapped_column(String(24))


def correction_seal_constraints(prefix, action_key):
    return (
        *request_constraints(prefix),
        *(UniqueConstraint(name, name='uq_' + prefix + '_' + suffix)
          for name, suffix in (('reversal_key_hash', 'reverse'),
                               ('approval_key_hash', 'approve'),
                               ('correction_key_hash', 'execute'))),
        ForeignKeyConstraint(['reversal_id', 'root_disposition_id'],
            ['stock_loss_disposition_reversals.id', 'stock_loss_disposition_reversals.root_disposition_id'],
            name='fk_' + prefix + '_inverse', ondelete='RESTRICT'),
        CheckConstraint('length(reversal_key_hash)=64 AND length(approval_key_hash)=64 '
            'AND length(correction_key_hash)=64 AND length(expected_reversal_hash)=64 '
            'AND length(request_reference)=82', name='ck_' + prefix + '_proof'),
        CheckConstraint('idempotency_key_hash=' + action_key, name='ck_' + prefix + '_action'),
        CheckConstraint(_binding_distinct, name='ck_' + prefix + '_distinct'),
        CheckConstraint('disposition IN ' + KINDS, name='ck_' + prefix + '_kind'),
    )


class StockLossCorrectionApprovalSeal(RequestContext, CorrectionSealKeys, CreatedAtMixin, Base):
    __tablename__ = 'stock_loss_correction_approval_seals'
    __table_args__ = correction_seal_constraints('loss_approval_seal', 'approval_key_hash')


class StockLossCorrectionExecutionSeal(RequestContext, CorrectionSealKeys, CreatedAtMixin, Base):
    __tablename__ = 'stock_loss_correction_execution_seals'
    __table_args__ = (
        *correction_seal_constraints('loss_execution_seal', 'correction_key_hash'),
        ForeignKeyConstraint(['correction_decision_id', 'root_disposition_id', 'reversal_id', 'disposition'],
            ['stock_loss_correction_decisions.id', 'stock_loss_correction_decisions.root_disposition_id',
             'stock_loss_correction_decisions.reversal_id', 'stock_loss_correction_decisions.disposition'],
            name='fk_loss_execution_seal_decision', ondelete='RESTRICT'),
        CheckConstraint('length(expected_correction_decision_hash)=64 AND length(plan_hash)=64',
            name='ck_loss_execution_seal_decision_hash'),
    )
    correction_decision_id: Mapped[UUID] = mapped_column(UUID_TYPE)
    expected_correction_decision_hash: Mapped[str] = mapped_column(String(64))
    plan_hash: Mapped[str] = mapped_column(String(64))
