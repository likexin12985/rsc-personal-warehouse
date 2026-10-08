"""Compose immutable original inputs onto the isolated condition candidate."""
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, String, Table, UniqueConstraint
from app.foundation_models import JSON_DOCUMENT
from app.stock_scrap_persistence_schema import identifier
from app.return_condition_schema import build_schema as condition_schema

NAME = 'stock_condition_submission_requests'


def define(metadata, *, allow_base=False):
    from app.database import Base
    if (metadata is Base.metadata and not allow_base) or NAME in metadata.tables or 'stock_condition_events' not in metadata.tables:
        raise ValueError('isolated complete condition schema without input registry required')
    return Table(NAME, metadata,
        identifier('event_id','stock_condition_events.id',primary=True,deferred=True),
        Column('created_at',DateTime(timezone=True),nullable=False),
        Column('actor_user_id',String(36),ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        identifier('actor_person_id','people.id'),
        Column('authorization_version',BigInteger,nullable=False),
        Column('request_id',String(160),nullable=False),
        Column('idempotency_key_hash',String(64),nullable=False),
        Column('material_sku_code',String(80),nullable=False),
        Column('input_jsonb',JSON_DOCUMENT,nullable=False),
        Column('input_hash',String(64),nullable=False),
        UniqueConstraint('actor_user_id','request_id',name='uq_condition_input_request'),
        UniqueConstraint('idempotency_key_hash',name='uq_condition_input_key'),
        CheckConstraint('authorization_version>0 AND length(request_id) BETWEEN 8 AND 160 '
            'AND length(material_sku_code) BETWEEN 1 AND 80 AND length(input_hash)=64 '
            'AND length(idempotency_key_hash)=64',name='ck_condition_input_context'))


def build_schema():
    metadata,tables,parents=condition_schema()
    return metadata,(*tables,define(metadata)),parents
