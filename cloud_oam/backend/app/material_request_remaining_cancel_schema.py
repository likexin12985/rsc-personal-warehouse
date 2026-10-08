"""Immutable compensation facts registered in formal 0171 metadata.

Approval, allocation and reservation history remain immutable. The terminal
cancellation covers the exact remaining demand after inventory is settled.
The forward migration must supply immutable/audit/authority/write barriers.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def define(metadata):
    cancellations = sa.Table('material_request_remaining_cancellations', metadata,
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('request_id', sa.Uuid(), sa.ForeignKey('material_requests.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('revision_id', sa.Uuid(), sa.ForeignKey('material_request_revisions.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('request_version', sa.BigInteger(), nullable=False),
        sa.Column('actor_user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('actor_person_id', sa.Uuid(), sa.ForeignKey('people.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('actor_role_assignment_id', sa.Uuid(), sa.ForeignKey('role_assignments.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('authorization_version', sa.BigInteger(), nullable=False),
        sa.Column('idempotency_key_hash', sa.String(64), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('evidence_sha256', sa.String(64), nullable=False),
        sa.Column('trace_request_id', sa.String(160), nullable=False),
        sa.Column('reason', sa.String(500), nullable=False),
        sa.Column('evidence_jsonb', sa.JSON().with_variant(JSONB(), 'postgresql'), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('request_id', name='uq_remaining_cancellations_request'),
        sa.UniqueConstraint('idempotency_key_hash', name='uq_remaining_cancellations_key'),
        sa.UniqueConstraint('actor_user_id', 'trace_request_id', name='uq_remaining_cancellations_trace'),
        sa.UniqueConstraint('id', 'request_id', 'revision_id', name='uq_remaining_cancellations_binding'),
        sa.CheckConstraint('request_version > 0 AND authorization_version > 0', name='ck_remaining_cancellations_versions'),
        sa.CheckConstraint('length(idempotency_key_hash)=64 AND length(request_hash)=64 AND length(evidence_sha256)=64', name='ck_remaining_cancellations_hashes'),
        sa.CheckConstraint('length(trim(reason)) > 0 AND length(trace_request_id) > 0', name='ck_remaining_cancellations_reason'),
        sa.CheckConstraint('created_at = occurred_at', name='ck_remaining_cancellations_time'))
    lines = sa.Table('material_request_remaining_cancellation_lines', metadata,
        sa.Column('cancellation_id', sa.Uuid(), primary_key=True),
        sa.Column('request_line_id', sa.Uuid(), sa.ForeignKey('material_request_lines.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('request_id', sa.Uuid(), nullable=False),
        sa.Column('revision_id', sa.Uuid(), nullable=False),
        sa.Column('cancelled_qty', sa.Numeric(18, 3), nullable=False),
        sa.ForeignKeyConstraint(['cancellation_id', 'request_id', 'revision_id'],
            ['material_request_remaining_cancellations.id', 'material_request_remaining_cancellations.request_id',
             'material_request_remaining_cancellations.revision_id'], ondelete='RESTRICT', name='fk_remaining_cancellation_lines_parent'),
        sa.UniqueConstraint('request_line_id', name='uq_remaining_cancellation_lines_line'),
        sa.CheckConstraint('cancelled_qty > 0', name='ck_remaining_cancellation_lines_quantity'))
    return cancellations, lines


metadata = sa.MetaData()
for name, kind in (('material_requests', sa.Uuid()), ('material_request_revisions', sa.Uuid()),
                   ('material_request_lines', sa.Uuid()), ('users', sa.String(36)),
                   ('people', sa.Uuid()), ('role_assignments', sa.Uuid())):
    sa.Table(name, metadata, sa.Column('id', kind, primary_key=True))
cancellations, cancellation_lines = define(metadata)
