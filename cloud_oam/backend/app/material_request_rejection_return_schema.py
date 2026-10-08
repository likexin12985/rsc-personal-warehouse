"""Forward rejection-return facts owned by frozen migration 0172."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def define(metadata):
    def ref(name, table, kind=None):
        return sa.Column(name, kind if kind is not None else sa.Uuid(), sa.ForeignKey(table+'.id', ondelete='RESTRICT'), nullable=False)
    returns = sa.Table('material_request_rejection_returns', metadata,
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('return_no', sa.String(100), nullable=False, unique=True),
        ref('request_id','material_requests'), ref('revision_id','material_request_revisions'),
        sa.Column('request_version', sa.BigInteger(), nullable=False),
        ref('receipt_id','receipts'), ref('receipt_line_id','receipt_lines'),
        ref('shipment_line_id','shipment_lines'), ref('outbound_posting_id','outbound_postings'),
        ref('reservation_id','stock_reservations'), ref('in_transit_account_id','stock_accounts'),
        ref('return_source_account_id','stock_accounts'),
        ref('actor_user_id','users',sa.String(36)), ref('actor_person_id','people'),
        ref('actor_role_assignment_id','role_assignments'),
        sa.Column('authorization_version',sa.BigInteger(),nullable=False),
        sa.Column('quantity',sa.Numeric(18,3),nullable=False),
        sa.Column('receipt_request_hash',sa.String(64),nullable=False),
        sa.Column('idempotency_key_hash',sa.String(64),nullable=False,unique=True),
        sa.Column('request_hash',sa.String(64),nullable=False),
        sa.Column('evidence_sha256',sa.String(64),nullable=False),
        sa.Column('trace_request_id',sa.String(160),nullable=False),
        sa.Column('reason',sa.String(500),nullable=False),
        sa.Column('evidence_jsonb',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False),
        sa.Column('occurred_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('id','receipt_line_id',name='uq_rejection_returns_binding'),
        sa.UniqueConstraint('actor_user_id','trace_request_id',name='uq_rejection_returns_trace'),
        sa.CheckConstraint('quantity > 0 AND request_version > 0 AND authorization_version > 0',name='ck_rejection_returns_quantity_version'),
        sa.CheckConstraint('created_at=occurred_at',name='ck_rejection_returns_time'),
        sa.CheckConstraint('in_transit_account_id <> return_source_account_id',name='ck_rejection_returns_accounts'),
        sa.CheckConstraint('length(trim(reason))>0',name='ck_rejection_returns_reason'),
        sa.Index('ix_rejection_returns_request','request_id','receipt_line_id'))
    serials = sa.Table('material_request_rejection_return_serials',metadata,
        sa.Column('return_id',sa.Uuid(),primary_key=True),
        sa.Column('receipt_line_id',sa.Uuid(),nullable=False),
        sa.Column('serial_id',sa.Uuid(),sa.ForeignKey('inventory_serials.id',ondelete='RESTRICT'),primary_key=True),
        sa.ForeignKeyConstraint(['return_id','receipt_line_id'],
            ['material_request_rejection_returns.id','material_request_rejection_returns.receipt_line_id'],ondelete='RESTRICT'))
    return returns,serials


metadata=sa.MetaData()
for name in ('material_requests','material_request_revisions','receipts','receipt_lines','shipment_lines',
             'outbound_postings','stock_reservations','stock_accounts','people','role_assignments','inventory_serials'):
    sa.Table(name,metadata,sa.Column('id',sa.Uuid(),primary_key=True))
sa.Table('users',metadata,sa.Column('id',sa.String(36),primary_key=True))
returns,return_serials=define(metadata)
