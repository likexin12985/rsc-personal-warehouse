"""Private seal schema candidate; no live Base mutation or runtime grants.

Only a DB-owned registrar may eventually create these rows from real client
keys. Structural constraints are not a substitute for canonical source,
authority, audit, cross-request fences or the formal migration.
"""
from sqlalchemy import Table, Column, String, CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from app.stock_scrap_persistence_schema import context, identifier, build_schema as stock_schema

NAME = 'stock_scrap_request_seals'
ALIASES = ('reversal_key_hash','approval_key_hash','correction_key_hash','recovery_key_hash','scrap_key_hash')
ANCHORS = ('root_disposition_id','original_decision_id','reversal_id','correction_decision_id',
    'scrap_line_id','recovery_request_id','regional_review_id','headquarters_review_id',
    'regional_decision','headquarters_decision','plan_hash')
REQUIRED = {
    'original': ('original_decision_id','plan_hash'),
    'correction': ('root_disposition_id','reversal_id','correction_decision_id','plan_hash'),
    'apply': ('root_disposition_id','scrap_line_id'),
    'regional': ('root_disposition_id','scrap_line_id','recovery_request_id'),
    'headquarters': ('root_disposition_id','scrap_line_id','recovery_request_id','regional_review_id','regional_decision'),
    'execute': ('root_disposition_id','scrap_line_id','recovery_request_id','headquarters_review_id','headquarters_decision','plan_hash'),
}


def define(metadata, *, allow_base=False):
    from app.database import Base
    if metadata is Base.metadata and not allow_base:
        raise ValueError('seal candidate must not modify live Base metadata')
    if NAME in metadata.tables:
        raise ValueError('seal candidate already defined')
    targets = dict(root_disposition_id='stock_loss_dispositions',original_decision_id='stock_loss_headquarters_decisions',
        reversal_id='stock_loss_disposition_reversals',correction_decision_id='stock_loss_correction_decisions',
        scrap_line_id='stock_scrap_lines',recovery_request_id='stock_scrap_recovery_requests',
        regional_review_id='stock_scrap_recovery_regional_reviews',headquarters_review_id='stock_scrap_recovery_headquarters_reviews')
    if not all(name in metadata.tables for name in (*targets.values(),'stock_operation_orders','stock_operation_lines')):
        raise ValueError('complete predecessor and scrap source metadata required')
    # Every optional column is explicitly null or non-null for each kind. SQL
    # CHECK must never accept an UNKNOWN result for a required source or plan.
    shapes = []
    for kind, required in REQUIRED.items():
        fields = ["kind='"+kind+"'"]
        fields.extend(name+' IS '+('NOT NULL' if name in required else 'NULL') for name in ANCHORS)
        shapes.append('('+' AND '.join(fields)+')')
    table = Table(NAME, metadata,
        *context('scrap_seal'), Column('kind',String(24),nullable=False),
        identifier('loss_operation_id','stock_operation_orders.id'),
        identifier('loss_line_id','stock_operation_lines.id'),
        *(identifier(name,target+'.id',nullable=True) for name,target in targets.items()),
        Column('scrap_disposition',String(24),nullable=False),
        Column('regional_decision',String(32)), Column('headquarters_decision',String(32)),
        Column('plan_hash',String(64)), Column('key_token',String(64),nullable=False),
        *(Column(name,String(64),nullable=False) for name in ALIASES),
        *(UniqueConstraint(name,name='uq_scrap_seal_'+name) for name in ('key_token',*ALIASES)),
        CheckConstraint(' OR '.join(shapes),name='ck_scrap_seal_sources'),
        CheckConstraint("scrap_disposition='scrap' AND (regional_decision IS NULL OR regional_decision='verified') "
            "AND (headquarters_decision IS NULL OR headquarters_decision='approve')",name='ck_scrap_seal_parent_decisions'),
        CheckConstraint("idempotency_key_hash=CASE WHEN kind IN ('original','correction') "
            "THEN scrap_key_hash ELSE recovery_key_hash END",name='ck_scrap_seal_actual_key'),
        CheckConstraint(' AND '.join(a+'<>'+b for i,a in enumerate(ALIASES) for b in ALIASES[i+1:]),
            name='ck_scrap_seal_distinct_aliases'),
        ForeignKeyConstraint(['reversal_id','root_disposition_id'],
            ['stock_loss_disposition_reversals.id','stock_loss_disposition_reversals.root_disposition_id'],
            name='fk_scrap_seal_inverse_root',ondelete='RESTRICT'),
        ForeignKeyConstraint(['correction_decision_id','root_disposition_id','reversal_id','scrap_disposition'],
            ['stock_loss_correction_decisions.id','stock_loss_correction_decisions.root_disposition_id',
             'stock_loss_correction_decisions.reversal_id','stock_loss_correction_decisions.disposition'],
            name='fk_scrap_seal_correction',ondelete='RESTRICT'),
        ForeignKeyConstraint(['scrap_line_id','root_disposition_id'],
            ['stock_scrap_lines.id','stock_scrap_lines.root_disposition_id'],name='fk_scrap_seal_scrap_root',ondelete='RESTRICT'),
        ForeignKeyConstraint(['recovery_request_id','scrap_line_id'],
            ['stock_scrap_recovery_requests.id','stock_scrap_recovery_requests.scrap_line_id'],
            name='fk_scrap_seal_application',ondelete='RESTRICT'),
        ForeignKeyConstraint(['regional_review_id','recovery_request_id','scrap_line_id','regional_decision'],
            ['stock_scrap_recovery_regional_reviews.id','stock_scrap_recovery_regional_reviews.recovery_request_id',
             'stock_scrap_recovery_regional_reviews.scrap_line_id','stock_scrap_recovery_regional_reviews.decision'],
            name='fk_scrap_seal_verified_region',ondelete='RESTRICT'),
        ForeignKeyConstraint(['headquarters_review_id','recovery_request_id','scrap_line_id','headquarters_decision'],
            ['stock_scrap_recovery_headquarters_reviews.id','stock_scrap_recovery_headquarters_reviews.recovery_request_id',
             'stock_scrap_recovery_headquarters_reviews.scrap_line_id','stock_scrap_recovery_headquarters_reviews.decision'],
            name='fk_scrap_seal_approved_headquarters',ondelete='RESTRICT'))
    digests = ('request_hash','idempotency_key_hash','key_token',*ALIASES)
    table.append_constraint(CheckConstraint(' AND '.join(name+" ~ '^[a-f0-9]{64}$'" for name in digests)
        + " AND (plan_hash IS NULL OR plan_hash ~ '^[a-f0-9]{64}$')",name='ck_scrap_seal_digests').ddl_if(dialect='postgresql'))
    table.append_constraint(CheckConstraint(' AND '.join("(length("+name+")=64 AND "+name+" NOT GLOB '*[^a-f0-9]*')" for name in digests)
        + " AND (plan_hash IS NULL OR (length(plan_hash)=64 AND plan_hash NOT GLOB '*[^a-f0-9]*'))",
        name='ck_scrap_seal_digests').ddl_if(dialect='sqlite'))
    table.append_constraint(CheckConstraint("request_id ~ '^[A-Za-z0-9._:-]{8,160}$'",
        name='ck_scrap_seal_request').ddl_if(dialect='postgresql'))
    table.append_constraint(CheckConstraint("length(request_id) BETWEEN 8 AND 160 AND request_id NOT GLOB '*[^A-Za-z0-9._:-]*'",
        name='ck_scrap_seal_request').ddl_if(dialect='sqlite'))
    return table


def build_schema():
    metadata, _, _ = stock_schema()
    return metadata, metadata.tables[NAME]
