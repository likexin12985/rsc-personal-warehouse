"""Isolated exact settlement inputs and captured scan identity, not a migration.

Immutable/current-capture/deferred-completeness SQL guards and service read
authorization remain mandatory before this schema may be installed for use.
"""
from sqlalchemy import (
    BigInteger, CheckConstraint, Column, DateTime, ForeignKey,
    ForeignKeyConstraint, String, Table, UniqueConstraint,
)
from app.foundation_models import JSON_DOCUMENT
from app.stock_scrap_persistence_schema import identifier
from app.return_condition_seal_schema import build_schema as condition_schema

NAME = 'stock_condition_settlement_requests'
SCANS = 'stock_condition_settlement_scans'


def reference(local, target, remote, name):
    return ForeignKeyConstraint(local, [target + '.' + key for key in remote],
        name=name, ondelete='RESTRICT', deferrable=True, initially='DEFERRED')


def define(metadata, *, allow_base=False):
    from app.database import Base
    if ((metadata is Base.metadata and not allow_base) or NAME in metadata.tables or SCANS in metadata.tables
            or not {'stock_condition_events', 'stock_condition_serials'} <= metadata.tables.keys()):
        raise ValueError('isolated complete condition schema without settlement inputs required')
    request = Table(NAME, metadata,
        identifier('event_id', primary=True),
        identifier('case_id'),
        Column('kind', String(24), nullable=False),
        identifier('previous_event_id'),
        Column('previous_kind', String(24), nullable=False),
        Column('previous_request_hash', String(64), nullable=False),
        Column('created_at', DateTime(timezone=True), nullable=False),
        Column('actor_user_id', String(36), ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        identifier('actor_person_id', 'people.id'),
        Column('authorization_version', BigInteger, nullable=False),
        Column('request_id', String(160), nullable=False),
        Column('idempotency_key_hash', String(64), nullable=False),
        Column('material_sku_code', String(80), nullable=False),
        Column('input_jsonb', JSON_DOCUMENT, nullable=False),
        Column('input_hash', String(64), nullable=False),
        reference(['event_id', 'case_id', 'kind'], 'stock_condition_events', ['id', 'case_id', 'kind'],
            'fk_condition_settlement_input_event'),
        reference(['previous_event_id', 'case_id', 'previous_kind'], 'stock_condition_events', ['id', 'case_id', 'kind'],
            'fk_condition_settlement_input_decision'),
        UniqueConstraint('event_id', 'case_id', name='uq_condition_settlement_input_case'),
        UniqueConstraint('actor_user_id', 'request_id', name='uq_condition_settlement_input_request'),
        UniqueConstraint('idempotency_key_hash', name='uq_condition_settlement_input_key'),
        CheckConstraint("(kind='execute' AND previous_kind='approve_hq') OR "
            "(kind='release' AND previous_kind IN ('reject_region','reject_hq','withdraw','cancel_approved'))",
            name='ck_condition_settlement_input_kind'),
        CheckConstraint('event_id<>previous_event_id AND authorization_version>0 '
            'AND length(request_id) BETWEEN 8 AND 160 AND length(material_sku_code) BETWEEN 1 AND 80 '
            'AND length(previous_request_hash)=64 AND length(idempotency_key_hash)=64 AND length(input_hash)=64',
            name='ck_condition_settlement_input_context'))
    scans = Table(SCANS, metadata,
        identifier('event_id', primary=True),
        identifier('serial_id', primary=True),
        identifier('case_id'),
        Column('created_at', DateTime(timezone=True), nullable=False),
        Column('sku_code', String(80), nullable=False),
        Column('serial_no', String(200), nullable=False),
        Column('qr_code', String(250), nullable=False),
        reference(['event_id', 'case_id'], NAME, ['event_id', 'case_id'], 'fk_condition_settlement_scan_input'),
        reference(['case_id', 'serial_id'], 'stock_condition_serials', ['case_id', 'serial_id'],
            'fk_condition_settlement_scan_selection'),
        CheckConstraint('length(sku_code) BETWEEN 1 AND 80 AND length(serial_no) BETWEEN 1 AND 200 '
            'AND length(qr_code) BETWEEN 1 AND 250', name='ck_condition_settlement_scan_context'))
    return request, scans


def build_schema():
    metadata, tables, parents = condition_schema()
    return metadata, (*tables, *define(metadata)), parents
