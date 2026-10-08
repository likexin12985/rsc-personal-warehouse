"""Isolated closure facts for unknown condition actions, including execute/release.

This table is not a migration or an authority to insert seals. A controlled
registrar must prove absence under locks, derive key aliases, bind canonical
input and audit, and install reciprocal late-write/immutability fences before
any API can use it. Closing a request never advances its business event.
"""
from sqlalchemy import BigInteger, CheckConstraint, Column, String, Table, UniqueConstraint

from app.return_condition_key_schema import ALIASES
from app.return_condition_schema import CASES, EVENTS, TRANSITIONS, _fk
from app.return_condition_seal_schema import build_schema as submission_seal_schema
from app.stock_scrap_binding_schema import digest_check
from app.stock_scrap_persistence_schema import context, identifier


NAME = 'stock_condition_decision_seals'
ACTIONS = ('supplement', 'withdraw', 'verify_region', 'return_evidence',
    'reject_region', 'return_region', 'reject_hq', 'approve_hq', 'cancel_approved',
    'execute', 'release')
SOURCE = ('root_disposition_id', 'inbound_id', 'inbound_line_id',
    'source_account_id', 'original_transaction_id', 'original_movement_id',
    'original_ledger_cursor')
SOURCE_CONSTRAINT = 'uq_condition_case_seal_source'


def define(metadata, *, allow_base=False):
    from app.database import Base
    required = (CASES, EVENTS, 'stock_condition_request_key_bindings',
        'stock_condition_request_seals')
    if ((metadata is Base.metadata and not allow_base) or NAME in metadata.tables
            or not all(name in metadata.tables for name in required)):
        raise ValueError('isolated complete condition seal schema required')
    case = metadata.tables[CASES]
    if any(c.name == SOURCE_CONSTRAINT for c in case.constraints):
        raise ValueError('decision seal source binding already installed')
    case.append_constraint(UniqueConstraint('id', *SOURCE, name=SOURCE_CONSTRAINT))
    table = Table(NAME, metadata, *context('cond_dec_seal'),
        Column('kind', String(24), nullable=False),
        identifier('case_id'),
        *(identifier(name) for name in SOURCE[:-1]),
        Column('original_ledger_cursor', BigInteger, nullable=False),
        identifier('expected_event_id'),
        Column('historical_state', String(32), nullable=False),
        Column('historical_sequence', BigInteger, nullable=False),
        # The client's original claim is preserved, not equated with actual
        # event.request_hash: even a failed preflight can leave an unknown key.
        Column('claimed_event_hash', String(64), nullable=False),
        Column('history_hash', String(64), nullable=False),
        Column('key_token', String(64), nullable=False),
        *(Column(name, String(64), nullable=False) for name in ALIASES),
        _fk(['case_id', *SOURCE], CASES, ['id', *SOURCE],
            'fk_cond_dec_seal_source'),
        _fk(['expected_event_id', 'case_id', 'historical_state', 'historical_sequence'],
            EVENTS, ['id', 'case_id', 'to_state', 'event_sequence'],
            'fk_cond_dec_seal_predecessor'),
        *(UniqueConstraint(name, name='uq_cond_dec_seal_' + name)
            for name in ('key_token', *ALIASES)),
        CheckConstraint('original_ledger_cursor>0 AND historical_sequence>0 '
            'AND idempotency_key_hash=condition_key_hash', name='ck_cond_dec_seal_context_binding'),
        CheckConstraint(' OR '.join("(kind='%s' AND historical_state='%s')" % (kind, before)
            for kind, before, _ in TRANSITIONS if kind in ACTIONS),
            name='ck_cond_dec_seal_action'),
        CheckConstraint(' AND '.join(a+'<>'+b for i, a in enumerate(ALIASES)
            for b in ALIASES[i+1:]), name='ck_cond_dec_seal_aliases'))
    for name in ('request_hash', 'idempotency_key_hash', 'claimed_event_hash',
            'history_hash', 'key_token', *ALIASES):
        digest_check(table, 'ck_cond_dec_seal_' + name, name)
    return table


def build_schema():
    metadata, tables, parents = submission_seal_schema()
    return metadata, (*tables, define(metadata)), parents
