"""Candidate relational model for historical return-condition correction.

Build on an isolated copy of the complete 0165 schema. Do not register in Base,
grant API writes or use this as a migration: ledger/audit/outbox completeness,
current authority, physical evidence, prefix budgets and append-only guards
still require the forward migration and its native business verification.
"""
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, ForeignKeyConstraint, Index,
    Numeric, String, Table, UniqueConstraint,
)

from app.foundation_models import JSON_DOCUMENT
from app.stock_scrap_persistence_schema import context, evidence_table, identifier


TYPE = 'condition_correction'
CASES = 'stock_condition_cases'
EVENTS = 'stock_condition_events'
SERIALS = 'stock_condition_serials'
FILES = 'stock_condition_files'
TABLE_NAMES = (CASES, EVENTS, SERIALS, FILES)
PARENT_NAMES = ('stock_operation_orders', 'stock_operation_lines',
                'stock_operation_return_inbound_lines', 'inventory_movements')

TRANSITIONS = (
    ('submit', 'draft', 'awaiting_regional'),
    ('verify_region', 'awaiting_regional', 'awaiting_headquarters'),
    ('return_evidence', 'awaiting_regional', 'needs_evidence'),
    ('supplement', 'needs_evidence', 'awaiting_regional'),
    ('return_region', 'awaiting_headquarters', 'awaiting_regional'),
    ('reject_region', 'awaiting_regional', 'rejected_pending_release'),
    ('reject_hq', 'awaiting_headquarters', 'rejected_pending_release'),
    ('approve_hq', 'awaiting_headquarters', 'approved'),
    ('withdraw', 'awaiting_regional', 'cancelled_pending_release'),
    ('withdraw', 'awaiting_headquarters', 'cancelled_pending_release'),
    ('withdraw', 'needs_evidence', 'cancelled_pending_release'),
    ('cancel_approved', 'approved', 'cancelled_pending_release'),
    ('release', 'rejected_pending_release', 'released_rejected'),
    ('release', 'cancelled_pending_release', 'released_cancelled'),
    ('execute', 'approved', 'executed'),
)


def _fk(local, table, remote, name, *, deferred=True):
    return ForeignKeyConstraint(local, [table + '.' + item for item in remote], name=name,
        ondelete='RESTRICT', deferrable=deferred, initially='DEFERRED' if deferred else None)


def _extend_check(table, name, alternative):
    matches = [c for c in table.constraints if isinstance(c, CheckConstraint) and c.name == name]
    if len(matches) != 1:
        raise ValueError('exact predecessor constraint required: ' + name)
    old = matches[0]
    table.constraints.remove(old)
    table.append_constraint(CheckConstraint(f'({old.sqltext}) OR ({alternative})', name=name))


def define(metadata, *, allow_base=False):
    from app.database import Base
    if (metadata is Base.metadata and not allow_base):
        raise ValueError('candidate cannot mutate live application metadata')
    required = (*PARENT_NAMES, 'stock_scrap_request_seals', 'stock_loss_dispositions',
                'inventory_transactions', 'inventory_serials', 'stock_accounts',
                'custody_assignments', 'people', 'users', 'files',
                'stock_operation_return_inbound_serials')
    if not all(name in metadata.tables for name in required):
        raise ValueError('complete 0165 predecessor schema required')
    if any(name in metadata.tables for name in TABLE_NAMES):
        raise ValueError('condition schema must be installed once as a complete set')

    order, line, inbound, movement = (metadata.tables[name] for name in PARENT_NAMES)
    _extend_check(order, 'ck_stock_operation_orders_type_status',
        "operation_type='condition_correction' AND status='submitted'")
    _extend_check(order, 'ck_stock_operation_orders_locations',
        "operation_type='condition_correction' AND oam_work_order_id IS NULL "
        "AND loss_headquarters_decision_id IS NULL AND loss_correction_decision_id IS NULL "
        "AND target_location_id IS NULL AND transit_location_id IS NULL "
        "AND target_custody_assignment_id IS NULL")
    order.append_column(identifier('condition_case_id', nullable=True, deferred=True))
    order.append_constraint(CheckConstraint(
        "(operation_type='condition_correction' AND condition_case_id IS NOT NULL AND condition_case_id=id) "
        "OR (operation_type<>'condition_correction' AND condition_case_id IS NULL)",
        name='ck_stock_operation_condition_case'))
    order.append_constraint(_fk(['condition_case_id', 'posting_transaction_id'], CASES,
        ['id', 'freeze_transaction_id'], 'fk_stock_operation_condition_case'))
    _extend_check(line, 'ck_stock_operation_lines_dimensions',
        "operation_type='condition_correction' AND source_recovery_line_id IS NULL "
        "AND source_loss_line_id IS NULL AND target_condition='damaged' "
        "AND stock_account_id<>reserved_account_id")
    line.append_column(identifier('condition_case_id', nullable=True))
    line.append_constraint(CheckConstraint(
        "(operation_type='condition_correction' AND condition_case_id IS NOT NULL AND condition_case_id=operation_id) "
        "OR (operation_type<>'condition_correction' AND condition_case_id IS NULL)",
        name='ck_stock_operation_line_condition_case'))
    line.append_constraint(_fk(['condition_case_id', 'id'], CASES,
        ['id', 'line_id'], 'fk_stock_operation_line_condition_case'))
    line.append_constraint(UniqueConstraint('id', 'operation_id', 'operation_type', 'stock_account_id',
        'reserved_account_id', 'quantity', 'target_condition', name='uq_condition_operation_line'))
    inbound.append_constraint(UniqueConstraint('id', 'inbound_id', 'target_account_id', 'condition_code',
        name='uq_condition_inbound_source'))
    movement.append_constraint(UniqueConstraint('id', 'transaction_id', 'from_account_id',
        'to_account_id', 'quantity', name='uq_condition_movement_exact'))

    case = Table(CASES, metadata,
        identifier('id', primary=True),
        Column('operation_type', String(24), nullable=False),
        identifier('line_id', 'stock_operation_lines.id'),
        identifier('root_disposition_id', 'stock_loss_dispositions.id'),
        identifier('inbound_id', 'stock_operation_return_inbounds.id'),
        identifier('inbound_line_id', 'stock_operation_return_inbound_lines.id'),
        identifier('original_transaction_id', 'inventory_transactions.id'),
        identifier('original_movement_id', 'inventory_movements.id'),
        Column('original_ledger_cursor', BigInteger, nullable=False),
        identifier('source_account_id', 'stock_accounts.id'),
        identifier('frozen_account_id', 'stock_accounts.id'),
        identifier('custody_assignment_id', 'custody_assignments.id'),
        Column('recorded_condition', String(24), nullable=False),
        Column('target_condition', String(24), nullable=False),
        Column('quantity', Numeric(18, 3), nullable=False),
        Column('affected_quantity', Numeric(18, 3), nullable=False),
        Column('tracking_mode', String(24), nullable=False),
        Column('quantity_scale', BigInteger, nullable=False),
        Column('allow_fraction', Boolean, nullable=False),
        Column('history_hash', String(64), nullable=False),
        Column('source_hash', String(64), nullable=False),
        Column('source_jsonb', JSON_DOCUMENT, nullable=False),
        identifier('submit_event_id'),
        Column('submit_kind', String(24), nullable=False),
        identifier('freeze_transaction_id', 'inventory_transactions.id', deferred=True),
        identifier('freeze_movement_id', 'inventory_movements.id', deferred=True),
        _fk(['id', 'operation_type'], 'stock_operation_orders', ['id', 'operation_type'],
            'fk_condition_case_order'),
        _fk(['id', 'freeze_transaction_id'], 'stock_operation_orders', ['id', 'posting_transaction_id'],
            'fk_condition_case_order_posting'),
        _fk(['line_id', 'id', 'operation_type', 'source_account_id', 'frozen_account_id', 'quantity', 'target_condition'],
            'stock_operation_lines', ['id', 'operation_id', 'operation_type', 'stock_account_id',
             'reserved_account_id', 'quantity', 'target_condition'], 'fk_condition_case_line'),
        _fk(['inbound_line_id', 'inbound_id', 'source_account_id', 'recorded_condition'],
            'stock_operation_return_inbound_lines', ['id', 'inbound_id', 'target_account_id', 'condition_code'],
            'fk_condition_case_inbound'),
        _fk(['original_movement_id', 'original_transaction_id'], 'inventory_movements',
            ['id', 'transaction_id'], 'fk_condition_case_original_movement'),
        _fk(['submit_event_id', 'id', 'submit_kind', 'freeze_transaction_id', 'freeze_movement_id'],
            EVENTS, ['id', 'case_id', 'kind', 'posting_transaction_id', 'posting_movement_id'],
            'fk_condition_case_submission'),
        _fk(['freeze_movement_id', 'freeze_transaction_id', 'source_account_id', 'frozen_account_id', 'quantity'],
            'inventory_movements', ['id', 'transaction_id', 'from_account_id', 'to_account_id', 'quantity'],
            'fk_condition_case_freeze'),
        UniqueConstraint('line_id', name='uq_condition_case_line'),
        UniqueConstraint('submit_event_id', name='uq_condition_case_submit'),
        UniqueConstraint('freeze_transaction_id', name='uq_condition_case_freeze_transaction'),
        UniqueConstraint('freeze_movement_id', name='uq_condition_case_freeze_movement'),
        UniqueConstraint('id', 'freeze_transaction_id', name='uq_condition_case_posting'),
        UniqueConstraint('id', 'line_id', name='uq_condition_case_order_line'),
        UniqueConstraint('id', 'submit_event_id', name='uq_condition_case_submit_event'),
        UniqueConstraint('id', 'inbound_line_id', name='uq_condition_case_inbound'),
        UniqueConstraint('id', 'inbound_line_id', 'source_account_id', 'frozen_account_id', 'quantity',
            name='uq_condition_case_share'),
        Index('ix_condition_case_inbound', 'inbound_line_id'),
        CheckConstraint("operation_type='condition_correction' AND submit_kind='submit' "
            "AND recorded_condition IN ('new','used') AND target_condition='damaged' "
            "AND source_account_id<>frozen_account_id AND quantity>0 AND affected_quantity>=quantity "
            "AND original_ledger_cursor>0 AND original_transaction_id<>freeze_transaction_id "
            "AND original_movement_id<>freeze_movement_id AND length(history_hash)=64 AND length(source_hash)=64",
            name='ck_condition_case_context'),
        CheckConstraint("tracking_mode IN ('none','lot','serial','lot_and_serial') "
            "AND quantity_scale BETWEEN 0 AND 3 AND "
            "(tracking_mode NOT IN ('serial','lot_and_serial') OR "
            "(quantity_scale=0 AND NOT allow_fraction AND quantity=CAST(quantity AS BIGINT) "
            "AND affected_quantity=CAST(affected_quantity AS BIGINT)))", name='ck_condition_case_policy'))

    event = Table(EVENTS, metadata,
        *context('condition_event'),
        identifier('case_id', CASES + '.id', deferred=True),
        identifier('submit_event_id'),
        identifier('inbound_line_id', 'stock_operation_return_inbound_lines.id'),
        identifier('source_account_id', 'stock_accounts.id'),
        identifier('frozen_account_id', 'stock_accounts.id'),
        Column('quantity', Numeric(18, 3), nullable=False),
        Column('event_sequence', BigInteger, nullable=False),
        Column('kind', String(24), nullable=False),
        Column('from_state', String(32), nullable=False),
        Column('to_state', String(32), nullable=False),
        identifier('previous_event_id', nullable=True),
        Column('previous_sequence', BigInteger, nullable=True),
        identifier('decision_event_id', nullable=True),
        Column('decision_kind', String(24), nullable=True),
        identifier('posting_transaction_id', 'inventory_transactions.id', nullable=True, deferred=True),
        identifier('posting_movement_id', 'inventory_movements.id', nullable=True, deferred=True),
        Column('movement_type', String(24), nullable=True),
        identifier('from_account_id', 'stock_accounts.id', nullable=True),
        identifier('to_account_id', 'stock_accounts.id', nullable=True),
        Column('plan_hash', String(64), nullable=False),
        Column('plan_jsonb', JSON_DOCUMENT, nullable=False),
        _fk(['case_id', 'inbound_line_id', 'source_account_id', 'frozen_account_id', 'quantity'], CASES,
            ['id', 'inbound_line_id', 'source_account_id', 'frozen_account_id', 'quantity'],
            'fk_condition_event_case_share'),
        _fk(['case_id', 'submit_event_id'], CASES, ['id', 'submit_event_id'],
            'fk_condition_event_submission'),
        _fk(['previous_event_id', 'case_id', 'from_state', 'previous_sequence'], EVENTS,
            ['id', 'case_id', 'to_state', 'event_sequence'], 'fk_condition_event_previous'),
        _fk(['decision_event_id', 'case_id', 'decision_kind'], EVENTS,
            ['id', 'case_id', 'kind'], 'fk_condition_event_decision'),
        _fk(['posting_movement_id', 'posting_transaction_id', 'from_account_id', 'to_account_id', 'quantity'],
            'inventory_movements', ['id', 'transaction_id', 'from_account_id', 'to_account_id', 'quantity'],
            'fk_condition_event_movement'),
        UniqueConstraint('inbound_line_id', 'event_sequence', name='uq_condition_event_sequence'),
        UniqueConstraint('previous_event_id', name='uq_condition_event_successor'),
        UniqueConstraint('posting_transaction_id', name='uq_condition_event_transaction'),
        UniqueConstraint('posting_movement_id', name='uq_condition_event_movement'),
        UniqueConstraint('id', 'case_id', 'kind', name='uq_condition_event_kind'),
        UniqueConstraint('id', 'case_id', 'to_state', 'event_sequence', name='uq_condition_event_state'),
        UniqueConstraint('id', 'case_id', 'kind', 'posting_transaction_id', 'posting_movement_id',
            name='uq_condition_event_posting'),
        Index('ix_condition_event_case', 'case_id', 'event_sequence'),
        CheckConstraint('event_sequence>0 AND quantity>0 AND length(plan_hash)=64', name='ck_condition_event_sequence_context'),
        CheckConstraint(' OR '.join("(kind='%s' AND from_state='%s' AND to_state='%s')" % row
            for row in TRANSITIONS), name='ck_condition_event_transition'),
        CheckConstraint("(kind='submit' AND id=submit_event_id AND previous_event_id IS NULL AND previous_sequence IS NULL) OR "
            "(kind<>'submit' AND previous_event_id IS NOT NULL AND previous_event_id<>id "
            "AND previous_sequence IS NOT NULL AND previous_sequence>0 AND event_sequence>previous_sequence)",
            name='ck_condition_event_previous'),
        CheckConstraint("(kind='execute' AND decision_event_id IS NOT NULL AND decision_kind IS NOT NULL "
            "AND decision_event_id=previous_event_id AND decision_kind='approve_hq') OR "
            "(kind='release' AND decision_event_id IS NOT NULL AND decision_kind IS NOT NULL "
            "AND decision_event_id=previous_event_id AND "
            "((from_state='rejected_pending_release' AND decision_kind IN ('reject_region','reject_hq')) "
            "OR (from_state='cancelled_pending_release' AND decision_kind IN ('withdraw','cancel_approved')))) OR "
            "(kind NOT IN ('execute','release') AND decision_event_id IS NULL AND decision_kind IS NULL)",
            name='ck_condition_event_decision'),
        CheckConstraint("(kind IN ('submit','execute','release') AND posting_transaction_id IS NOT NULL "
            "AND posting_movement_id IS NOT NULL AND movement_type IS NOT NULL AND from_account_id IS NOT NULL "
            "AND to_account_id IS NOT NULL AND from_account_id<>to_account_id AND "
            "((kind='submit' AND movement_type='freeze' AND from_account_id=source_account_id AND to_account_id=frozen_account_id) "
            "OR (kind='release' AND movement_type='unfreeze' AND from_account_id=frozen_account_id AND to_account_id=source_account_id) "
            "OR (kind='execute' AND movement_type='status_change' AND from_account_id=frozen_account_id "
            "AND to_account_id<>source_account_id))) OR "
            "(kind NOT IN ('submit','execute','release') AND posting_transaction_id IS NULL "
            "AND posting_movement_id IS NULL AND movement_type IS NULL AND from_account_id IS NULL AND to_account_id IS NULL)",
            name='ck_condition_event_posting'))

    serial = Table(SERIALS, metadata,
        identifier('case_id', CASES + '.id', primary=True),
        identifier('serial_id', 'inventory_serials.id', primary=True),
        identifier('inbound_line_id', 'stock_operation_return_inbound_lines.id'),
        _fk(['case_id', 'inbound_line_id'], CASES, ['id', 'inbound_line_id'], 'fk_condition_serial_case'),
        _fk(['inbound_line_id', 'serial_id'], 'stock_operation_return_inbound_serials',
            ['line_id', 'serial_id'], 'fk_condition_serial_original'))
    files = evidence_table(metadata, FILES, 'event_id', EVENTS)
    return (case, event, serial, files), (order, line, inbound, movement)


def build_schema():
    from app import models  # noqa: F401 - load the complete current model once
    from app.return_condition_application_schema import predecessor_schema
    metadata = predecessor_schema()
    tables, parents = define(metadata)
    return metadata, tables, parents
