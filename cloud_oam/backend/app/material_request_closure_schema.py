"""Append-only request closure table, activated by its forward migration.

Separate metadata keeps an unactivated forward schema out of the current
application's create-all and runtime admission. Migration activation must copy
this reviewed definition into the frozen migration and production metadata.
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def define(metadata):
    return sa.Table('material_request_closures', metadata,
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
        sa.UniqueConstraint('request_id', name='uq_material_request_closures_request'),
        sa.UniqueConstraint('idempotency_key_hash', name='uq_material_request_closures_key'),
        sa.UniqueConstraint('actor_user_id', 'trace_request_id', name='uq_material_request_closures_trace'),
        sa.CheckConstraint('request_version > 0 AND authorization_version > 0', name='ck_material_request_closures_versions'),
        sa.CheckConstraint('length(idempotency_key_hash)=64 AND length(request_hash)=64 AND length(evidence_sha256)=64', name='ck_material_request_closures_hashes'),
        sa.CheckConstraint('length(trim(reason)) > 0 AND length(trace_request_id) > 0', name='ck_material_request_closures_reason'),
        sa.CheckConstraint('created_at = occurred_at', name='ck_material_request_closures_time'))


# Core DML does not create tables. References are resolved only for the DDL
# tooling; these stubs are never created, queried, or registered in Base.
metadata = sa.MetaData()
for name, kind in (('material_requests', sa.Uuid()), ('material_request_revisions', sa.Uuid()),
                   ('users', sa.String(36)), ('people', sa.Uuid()), ('role_assignments', sa.Uuid())):
    sa.Table(name, metadata, sa.Column('id', kind, primary_key=True))
closures = define(metadata)
