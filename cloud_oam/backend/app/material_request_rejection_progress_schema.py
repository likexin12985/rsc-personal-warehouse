"""Forward progress facts owned by frozen formal migration 0173.

Cancellation preserves the original registration. Departure and handover do not
post inventory or establish return receipt/warehouse acceptance.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def define(metadata):
    def ref(name, table, kind=None):
        return sa.Column(name, kind if kind is not None else sa.Uuid(),
                         sa.ForeignKey(table + '.id', ondelete='RESTRICT'), nullable=False)

    return sa.Table('material_request_rejection_progress', metadata,
        sa.Column('id', sa.Uuid(), primary_key=True),
        ref('return_id', 'material_request_rejection_returns'),
        ref('request_id', 'material_requests'),
        sa.Column('request_version', sa.BigInteger(), nullable=False),
        sa.Column('action', sa.String(32), nullable=False),
        ref('actor_user_id', 'users', sa.String(36)),
        ref('actor_person_id', 'people'),
        ref('actor_role_assignment_id', 'role_assignments'),
        sa.Column('authorization_version', sa.BigInteger(), nullable=False),
        sa.Column('registration_request_hash', sa.String(64), nullable=False),
        sa.Column('previous_event_id', sa.Uuid()),
        sa.Column('previous_request_hash', sa.String(64)),
        sa.Column('physical_at', sa.DateTime(timezone=True)),
        sa.Column('carrier', sa.String(100)),
        sa.Column('tracking_no', sa.String(100)),
        sa.Column('reason', sa.String(500), nullable=False),
        sa.Column('idempotency_key_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('trace_request_id', sa.String(160), nullable=False),
        sa.Column('evidence_sha256', sa.String(64), nullable=False),
        sa.Column('evidence_jsonb', sa.JSON().with_variant(JSONB(), 'postgresql'), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('id', 'return_id', name='uq_rejection_progress_parent'),
        sa.ForeignKeyConstraint(['previous_event_id', 'return_id'],
            ['material_request_rejection_progress.id', 'material_request_rejection_progress.return_id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('return_id', 'action', name='uq_rejection_progress_action'),
        sa.UniqueConstraint('actor_user_id', 'trace_request_id', name='uq_rejection_progress_trace'),
        sa.CheckConstraint("action IN ('cancel_registration','depart','handover')", name='ck_rejection_progress_action'),
        sa.CheckConstraint('request_version > 0 AND authorization_version > 0', name='ck_rejection_progress_version'),
        sa.CheckConstraint('length(trim(reason)) > 0', name='ck_rejection_progress_reason'),
        sa.CheckConstraint("(action='cancel_registration' AND physical_at IS NULL) OR "
            "(action IN ('depart','handover') AND physical_at IS NOT NULL AND physical_at<=recorded_at)",
            name='ck_rejection_progress_physical'),
        sa.CheckConstraint("(action='handover' AND previous_event_id IS NOT NULL AND previous_request_hash IS NOT NULL "
            "AND carrier IS NOT NULL AND tracking_no IS NOT NULL AND length(trim(carrier))>0 AND length(trim(tracking_no))>0) OR "
            "(action<>'handover' AND previous_event_id IS NULL AND previous_request_hash IS NULL AND carrier IS NULL AND tracking_no IS NULL)",
            name='ck_rejection_progress_predecessor'),
        sa.Index('ix_rejection_progress_request', 'request_id', 'return_id'))


metadata = sa.MetaData()
for name in ('material_request_rejection_returns', 'material_requests', 'people', 'role_assignments'):
    sa.Table(name, metadata, sa.Column('id', sa.Uuid(), primary_key=True))
sa.Table('users', metadata, sa.Column('id', sa.String(36), primary_key=True))
progress = define(metadata)
