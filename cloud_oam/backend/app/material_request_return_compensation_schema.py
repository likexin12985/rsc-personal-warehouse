"""Immutable returned-demand compensation facts registered by formal migration 0176."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def define(metadata):
    def ref(name, target, kind=None, **kwargs):
        return sa.Column(name, kind if kind is not None else sa.Uuid(),
            sa.ForeignKey(target + '.id', ondelete='RESTRICT'), nullable=False, **kwargs)
    return sa.Table('material_request_return_compensations', metadata,
        sa.Column('id', sa.Uuid(), primary_key=True),
        ref('inbound_id', 'material_request_rejection_inbounds', unique=True),
        ref('request_id', 'material_requests'), ref('revision_id', 'material_request_revisions'),
        ref('request_line_id', 'material_request_lines'),
        sa.Column('request_version', sa.BigInteger(), nullable=False),
        ref('actor_user_id', 'users', sa.String(36)), ref('actor_person_id', 'people'),
        ref('actor_role_assignment_id', 'role_assignments'),
        sa.Column('authorization_version', sa.BigInteger(), nullable=False),
        sa.Column('cancelled_qty', sa.Numeric(18, 3), nullable=False),
        sa.Column('reason', sa.String(500), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('idempotency_key_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('trace_request_id', sa.String(160), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('evidence_sha256', sa.String(64), nullable=False),
        sa.Column('evidence_jsonb', sa.JSON().with_variant(JSONB(), 'postgresql'), nullable=False),
        sa.ForeignKeyConstraint(['request_line_id', 'request_id', 'revision_id'],
            ['material_request_lines.id', 'material_request_lines.request_id', 'material_request_lines.revision_id'],
            name='fk_return_compensation_line', ondelete='RESTRICT'),
        sa.UniqueConstraint('actor_user_id', 'trace_request_id', name='uq_return_compensation_trace'),
        sa.CheckConstraint('cancelled_qty>0 AND request_version>0 AND authorization_version>0', name='ck_return_compensation_quantity_version'),
        sa.CheckConstraint('length(trim(reason))>0', name='ck_return_compensation_reason'),
        sa.Index('ix_return_compensation_request', 'request_id', 'request_line_id'))


metadata = sa.MetaData()
for name in ('material_request_rejection_inbounds', 'material_requests', 'material_request_revisions', 'people', 'role_assignments'):
    sa.Table(name, metadata, sa.Column('id', sa.Uuid(), primary_key=True))
sa.Table('material_request_lines', metadata, sa.Column('id', sa.Uuid(), primary_key=True),
    sa.Column('request_id', sa.Uuid(), nullable=False), sa.Column('revision_id', sa.Uuid(), nullable=False),
    sa.UniqueConstraint('id', 'request_id', 'revision_id'))
sa.Table('users', metadata, sa.Column('id', sa.String(36), primary_key=True))
compensations = define(metadata)
