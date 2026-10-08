"""Isolated database-owned condition key registry; no live Base activation."""
from sqlalchemy import Column, DateTime, ForeignKey, String, Table, UniqueConstraint, CheckConstraint
from app.stock_scrap_binding_schema import digest_check
from app.stock_scrap_persistence_schema import identifier
from app.return_condition_request_schema import build_schema as input_schema

NAME = 'stock_condition_request_key_bindings'
KEY_DOMAINS = (
    ('raw_key_hash', ''),
    ('regional_key_hash', 'stock-loss-regional-review:'),
    ('headquarters_key_hash', 'stock-loss-headquarters-review:'),
    ('disposition_key_hash', 'stock-loss-disposition:'),
    ('return_key_hash', 'stock-loss-return:'),
    ('reversal_key_hash', 'stock-loss:reverse_loss:'),
    ('approval_key_hash', 'stock-loss:approve_loss_correction:'),
    ('correction_key_hash', 'stock-loss:correct_loss:'),
    ('recovery_key_hash', 'stock-scrap-recovery:'),
    ('scrap_key_hash', 'stock-scrap:'),
    ('condition_key_hash', 'stock-condition:'),
)
ALIASES = tuple(name for name, _ in KEY_DOMAINS)
PREFIXES = tuple(prefix for _, prefix in KEY_DOMAINS)


def define(metadata, *, allow_base=False):
    from app.database import Base
    if (metadata is Base.metadata and not allow_base) or NAME in metadata.tables or 'stock_condition_submission_requests' not in metadata.tables:
        raise ValueError('isolated condition schema with original inputs required')
    table = Table(NAME, metadata,
        identifier('event_id','stock_condition_events.id',primary=True,deferred=True),
        identifier('case_id','stock_condition_cases.id',deferred=True),
        Column('actor_user_id',String(36),ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        identifier('actor_person_id','people.id'),
        Column('request_id',String(160),nullable=False), Column('request_hash',String(64),nullable=False),
        Column('key_token',String(64),nullable=False),
        *(Column(name,String(64),nullable=False) for name in ALIASES),
        Column('created_at',DateTime(timezone=True),nullable=False),
        UniqueConstraint('actor_user_id','request_id',name='uq_condition_key_request'),
        *(UniqueConstraint(name,name='uq_condition_binding_'+name) for name in ('key_token',*ALIASES)),
        CheckConstraint(' AND '.join(a+'<>'+b for i,a in enumerate(ALIASES) for b in ALIASES[i+1:]),
            name='ck_condition_binding_distinct_aliases'),
        CheckConstraint('length(request_id) BETWEEN 8 AND 160',name='ck_condition_binding_request'))
    for name in ('request_hash','key_token',*ALIASES):
        digest_check(table,'ck_condition_binding_'+name,name)
    return table


def build_schema():
    metadata,tables,parents = input_schema()
    return metadata,(*tables,define(metadata)),parents
