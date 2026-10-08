"""Isolated seventh condition table; absence never fabricates a stock event."""
from sqlalchemy import BigInteger, CheckConstraint, Column, String, Table, UniqueConstraint
from app.stock_scrap_persistence_schema import context, identifier
from app.stock_scrap_binding_schema import digest_check
from app.return_condition_key_schema import ALIASES, build_schema as key_schema

NAME = 'stock_condition_request_seals'
ANCHORS = dict(root_disposition_id='stock_loss_dispositions',
    inbound_id='stock_operation_return_inbounds', inbound_line_id='stock_operation_return_inbound_lines',
    source_account_id='stock_accounts', original_transaction_id='inventory_transactions',
    original_movement_id='inventory_movements')


def define(metadata, *, allow_base=False):
    from app.database import Base
    if (metadata is Base.metadata and not allow_base) or NAME in metadata.tables or 'stock_condition_request_key_bindings' not in metadata.tables:
        raise ValueError('isolated complete condition registry required')
    table = Table(NAME, metadata, *context('condition_seal'),
        Column('kind', String(24), nullable=False),
        *(identifier(name, target + '.id') for name, target in ANCHORS.items()),
        Column('original_ledger_cursor', BigInteger, nullable=False),
        Column('key_token', String(64), nullable=False),
        *(Column(name, String(64), nullable=False) for name in ALIASES),
        *(UniqueConstraint(name, name='uq_condition_seal_' + name) for name in ('key_token', *ALIASES)),
        CheckConstraint("kind='submit' AND original_ledger_cursor>0 AND idempotency_key_hash=condition_key_hash",
            name='ck_condition_seal_kind'),
        CheckConstraint(' AND '.join(a+'<>'+b for i,a in enumerate(ALIASES) for b in ALIASES[i+1:]),
            name='ck_condition_seal_aliases'))
    for name in ('request_hash', 'idempotency_key_hash', 'key_token', *ALIASES):
        digest_check(table, 'ck_condition_seal_' + name, name)
    return table


def build_schema():
    metadata, tables, parents = key_schema()
    return metadata, (*tables, define(metadata)), parents
