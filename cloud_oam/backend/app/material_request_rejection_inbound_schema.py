"""Independent warehouse posting facts; registered by formal migration 0175."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def define(metadata):
    def ref(name, target, kind=None, **kwargs):
        return sa.Column(name, kind if kind is not None else sa.Uuid(),
            sa.ForeignKey(target + '.id', ondelete='RESTRICT'), nullable=False, **kwargs)
    inbounds = sa.Table('material_request_rejection_inbounds', metadata,
        sa.Column('id', sa.Uuid(), primary_key=True),
        ref('receipt_id', 'material_request_rejection_receipts', unique=True),
        ref('return_id', 'material_request_rejection_returns'),
        ref('request_id', 'material_requests'),
        sa.Column('request_version', sa.BigInteger(), nullable=False),
        ref('source_account_id', 'stock_accounts'), ref('target_location_id', 'stock_locations'),
        ref('custody_assignment_id', 'custody_assignments'),
        ref('actor_user_id', 'users', sa.String(36)), ref('actor_person_id', 'people'),
        ref('actor_role_assignment_id', 'role_assignments'),
        sa.Column('authorization_version', sa.BigInteger(), nullable=False),
        ref('posting_transaction_id', 'inventory_transactions', unique=True),
        sa.Column('accepted_qty', sa.Numeric(18, 3), nullable=False),
        sa.Column('damaged_qty', sa.Numeric(18, 3), nullable=False),
        sa.Column('reason', sa.String(500), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('idempotency_key_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('trace_request_id', sa.String(160), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('plan_hash', sa.String(64), nullable=False),
        sa.Column('plan_jsonb', sa.JSON().with_variant(JSONB(), 'postgresql'), nullable=False),
        sa.ForeignKeyConstraint(['receipt_id', 'return_id'],
            ['material_request_rejection_receipts.id', 'material_request_rejection_receipts.return_id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('actor_user_id', 'trace_request_id', name='uq_rejection_inbound_trace'),
        sa.CheckConstraint('accepted_qty>0 AND damaged_qty>=0 AND damaged_qty<=accepted_qty', name='ck_rejection_inbound_quantity'),
        sa.CheckConstraint('request_version>0 AND authorization_version>0', name='ck_rejection_inbound_versions'),
        sa.CheckConstraint('length(trim(reason))>0', name='ck_rejection_inbound_reason'),
        sa.Index('ix_rejection_inbounds_return', 'return_id', 'recorded_at'))
    parts = sa.Table('material_request_rejection_inbound_parts', metadata,
        sa.Column('inbound_id', sa.Uuid(), sa.ForeignKey(inbounds.c.id, ondelete='RESTRICT'), primary_key=True),
        sa.Column('condition_code', sa.String(16), primary_key=True),
        ref('target_account_id', 'stock_accounts'),
        sa.Column('quantity', sa.Numeric(18, 3), nullable=False),
        sa.CheckConstraint("condition_code IN ('new','used','damaged') AND quantity>0", name='ck_rejection_inbound_part'))
    serials = sa.Table('material_request_rejection_inbound_serials', metadata,
        sa.Column('inbound_id', sa.Uuid(), primary_key=True),
        sa.Column('serial_id', sa.Uuid(), sa.ForeignKey('inventory_serials.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('condition_code', sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(['inbound_id', 'condition_code'],
            [parts.c.inbound_id, parts.c.condition_code], ondelete='RESTRICT'))
    return inbounds, parts, serials


metadata = sa.MetaData()
for name in ('material_request_rejection_returns', 'material_requests',
        'stock_accounts', 'stock_locations', 'custody_assignments', 'people', 'role_assignments',
        'inventory_transactions', 'inventory_serials'):
    sa.Table(name, metadata, sa.Column('id', sa.Uuid(), primary_key=True))
sa.Table('users', metadata, sa.Column('id', sa.String(36), primary_key=True))
sa.Table('material_request_rejection_receipts', metadata, sa.Column('id', sa.Uuid(), primary_key=True),
    sa.Column('return_id', sa.Uuid(), nullable=False), sa.UniqueConstraint('id', 'return_id'))
inbounds, parts, serials = define(metadata)
