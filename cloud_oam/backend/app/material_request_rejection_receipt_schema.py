"""Formal 0174 warehouse acceptance facts, independent of inventory posting."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def define(metadata):
    def ref(name, table, kind=None):
        return sa.Column(name, kind if kind is not None else sa.Uuid(), sa.ForeignKey(table+'.id', ondelete='RESTRICT'), nullable=False)
    receipts = sa.Table('material_request_rejection_receipts', metadata,
        sa.Column('id', sa.Uuid(), primary_key=True),
        ref('return_id','material_request_rejection_returns'), ref('request_id','material_requests'),
        sa.Column('request_version',sa.BigInteger(),nullable=False),
        sa.Column('handover_id',sa.Uuid(),nullable=False),
        sa.Column('registration_request_hash',sa.String(64),nullable=False),
        sa.Column('handover_request_hash',sa.String(64),nullable=False),
        ref('target_location_id','stock_locations'), ref('custody_assignment_id','custody_assignments'),
        ref('actor_user_id','users',sa.String(36)), ref('actor_person_id','people'),
        ref('actor_role_assignment_id','role_assignments'), sa.Column('authorization_version',sa.BigInteger(),nullable=False),
        *(sa.Column(n,sa.Numeric(18,3),nullable=False) for n in ('accepted_qty','rejected_qty','damaged_qty','shortage_qty')),
        sa.Column('received_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('recorded_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('reason',sa.String(500),nullable=False),
        sa.Column('observed_sku_code',sa.String(80)),
        sa.Column('idempotency_key_hash',sa.String(64),nullable=False,unique=True),
        sa.Column('trace_request_id',sa.String(160),nullable=False),
        sa.Column('request_hash',sa.String(64),nullable=False),
        sa.Column('evidence_sha256',sa.String(64),nullable=False),
        sa.Column('evidence_jsonb',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False),
        sa.ForeignKeyConstraint(['handover_id','return_id'],['material_request_rejection_progress.id','material_request_rejection_progress.return_id'],ondelete='RESTRICT'),
        sa.UniqueConstraint('id','return_id',name='uq_rejection_receipts_parent'),
        sa.UniqueConstraint('actor_user_id','trace_request_id',name='uq_rejection_receipts_trace'),
        sa.CheckConstraint('accepted_qty>=0 AND rejected_qty>=0 AND damaged_qty>=0 AND shortage_qty>=0 AND damaged_qty<=accepted_qty AND accepted_qty+rejected_qty+shortage_qty>0',name='ck_rejection_receipts_quantities'),
        sa.CheckConstraint('request_version>0 AND authorization_version>0 AND received_at<=recorded_at',name='ck_rejection_receipts_time_version'),
        sa.CheckConstraint('length(trim(reason))>0',name='ck_rejection_receipts_reason'),
        sa.CheckConstraint("(accepted_qty>0 AND observed_sku_code IS NOT NULL AND length(trim(observed_sku_code))>0) OR (accepted_qty=0 AND observed_sku_code IS NULL)",name='ck_rejection_receipts_sku'),
        sa.Index('ix_rejection_receipts_return','return_id','recorded_at'))
    serials = sa.Table('material_request_rejection_receipt_serials', metadata,
        sa.Column('receipt_id',sa.Uuid(),primary_key=True),sa.Column('return_id',sa.Uuid(),nullable=False),
        sa.Column('serial_id',sa.Uuid(),sa.ForeignKey('inventory_serials.id',ondelete='RESTRICT'),primary_key=True),
        sa.Column('result',sa.String(16),nullable=False),sa.Column('damaged',sa.Boolean(),nullable=False),
        sa.Column('sku_code',sa.String(80)),sa.Column('serial_no',sa.String(200)),sa.Column('qr_code',sa.String(250)),
        sa.ForeignKeyConstraint(['receipt_id','return_id'],['material_request_rejection_receipts.id','material_request_rejection_receipts.return_id'],ondelete='RESTRICT'),
        sa.CheckConstraint("result IN ('accepted','rejected','shortage') AND (NOT damaged OR result='accepted')",name='ck_rejection_receipt_serial_result'),
        sa.CheckConstraint("(result='accepted' AND sku_code IS NOT NULL AND serial_no IS NOT NULL AND qr_code IS NOT NULL) OR (result<>'accepted' AND sku_code IS NULL AND serial_no IS NULL AND qr_code IS NULL)",name='ck_rejection_receipt_serial_scan'),
        sa.Index('uq_rejection_receipt_serial_confirmed','return_id','serial_id',unique=True,
            postgresql_where=sa.text("result IN ('accepted','rejected')"),sqlite_where=sa.text("result IN ('accepted','rejected')")))
    exceptions = sa.Table('material_request_rejection_receipt_exceptions', metadata,
        sa.Column('receipt_id',sa.Uuid(),sa.ForeignKey(receipts.c.id,ondelete='RESTRICT'),primary_key=True),
        sa.Column('exception_type',sa.String(32),primary_key=True),
        ref('evidence_file_id','files'),sa.Column('description',sa.String(1000),nullable=False),
        sa.CheckConstraint("exception_type IN ('shortage','damaged','wrong_material','wrong_serial','rejected')",name='ck_rejection_receipt_exception_type'))
    return receipts,serials,exceptions


metadata=sa.MetaData()
for name in ('material_request_rejection_returns','material_requests','stock_locations','custody_assignments','people','role_assignments','inventory_serials','files'):
    sa.Table(name,metadata,sa.Column('id',sa.Uuid(),primary_key=True))
sa.Table('users',metadata,sa.Column('id',sa.String(36),primary_key=True))
sa.Table('material_request_rejection_progress',metadata,sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('return_id',sa.Uuid(),nullable=False),sa.UniqueConstraint('id','return_id'))
receipts,serials,exceptions=define(metadata)
