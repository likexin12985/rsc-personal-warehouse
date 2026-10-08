"""Candidate scrap/recovery facts; not installed in application metadata.

An explicit metadata copy is required. The forward migration must also extend
the four existing parent schemas, add bidirectional deferred stock/event
proofs, append-only fences and least-privilege grants. These tables alone do
not authorize posting or prove the completeness of a business transaction.
"""
from sqlalchemy import (Table, Column, BigInteger, String, Text, Numeric, DateTime,
    CheckConstraint, ForeignKey, ForeignKeyConstraint, UniqueConstraint)
from app.foundation_models import UUID_TYPE, JSON_DOCUMENT


def identifier(name, target=None, *, nullable=False, deferred=False, primary=False):
    args = (ForeignKey(target, ondelete='RESTRICT',
        deferrable=deferred, initially='DEFERRED' if deferred else None),) if target else ()
    return Column(name, UUID_TYPE, *args, primary_key=primary, nullable=nullable)


def context(prefix):
    """Immutable request identity; business authority is independently proved."""
    return (
        identifier('id', primary=True),
        Column('created_at', DateTime(timezone=True), nullable=False),
        Column('actor_user_id', String(36), ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        identifier('actor_person_id', 'people.id'),
        Column('authorization_version', BigInteger, nullable=False),
        Column('request_id', String(160), nullable=False),
        Column('idempotency_key_hash', String(64), nullable=False),
        Column('request_hash', String(64), nullable=False),
        Column('reason', Text, nullable=False),
        Column('command_jsonb', JSON_DOCUMENT, nullable=False),
        UniqueConstraint('actor_user_id', 'request_id', name='uq_' + prefix + '_request'),
        UniqueConstraint('idempotency_key_hash', name='uq_' + prefix + '_key'),
        CheckConstraint('authorization_version > 0 AND length(reason) BETWEEN 1 AND 500 '
            'AND length(request_id) BETWEEN 8 AND 160 AND length(request_hash)=64 '
            'AND length(idempotency_key_hash)=64', name='ck_' + prefix + '_context'),
    )


def evidence_table(metadata, name, parent_column, parent_table):
    return Table(name, metadata,
        identifier(parent_column, parent_table + '.id', primary=True),
        identifier('file_id', 'files.id', primary=True),
        Column('metadata_sha256', String(64), nullable=False),
        Column('created_at', DateTime(timezone=True), nullable=False),
        UniqueConstraint('file_id', name='uq_' + name + '_file'),
        CheckConstraint('length(metadata_sha256)=64', name='ck_' + name + '_digest'))


def define(metadata, *, allow_base=False):
    """Build against a copy of the full existing schema, never Base.metadata."""
    from app.database import Base
    if metadata is Base.metadata and not allow_base:
        raise ValueError('candidate must not modify the live application metadata')
    required = ('stock_operation_orders', 'stock_operation_lines', 'stock_loss_dispositions',
        'stock_loss_headquarters_decisions', 'stock_loss_disposition_reversals',
        'stock_loss_correction_decisions', 'stock_loss_correction_executions',
        'inventory_transactions', 'inventory_movements', 'inventory_serials',
        'stock_accounts', 'custody_assignments', 'users', 'people', 'files')
    if not all(name in metadata.tables for name in required):
        raise ValueError('complete existing schema required')
    if any(name.startswith('stock_scrap_') for name in metadata.tables):
        raise ValueError('scrap facts already defined')

    line = Table('stock_scrap_lines', metadata,
        identifier('id', primary=True),
        Column('created_at', DateTime(timezone=True), nullable=False),
        identifier('operation_id'),
        Column('operation_type', String(24), nullable=False),
        ForeignKeyConstraint(['operation_id', 'operation_type'],
            ['stock_operation_orders.id', 'stock_operation_orders.operation_type'],
            name='fk_scrap_line_order', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'),
        identifier('loss_line_id', 'stock_operation_lines.id'),
        identifier('root_disposition_id', 'stock_loss_dispositions.id', deferred=True),
        Column('source_kind', String(24), nullable=False),
        identifier('original_decision_id', 'stock_loss_headquarters_decisions.id', nullable=True),
        identifier('correction_decision_id', 'stock_loss_correction_decisions.id', nullable=True),
        identifier('predecessor_reversal_id', 'stock_loss_disposition_reversals.id', nullable=True),
        identifier('correction_execution_id', 'stock_loss_correction_executions.id', nullable=True, deferred=True),
        identifier('frozen_account_id', 'stock_accounts.id'),
        identifier('custody_assignment_id', 'custody_assignments.id'),
        Column('quantity', Numeric(18, 3), nullable=False),
        identifier('posting_transaction_id', 'inventory_transactions.id', deferred=True),
        identifier('posting_movement_id', 'inventory_movements.id', deferred=True),
        ForeignKeyConstraint(['operation_id', 'posting_transaction_id'],
            ['stock_operation_orders.id', 'stock_operation_orders.posting_transaction_id'],
            name='fk_scrap_line_order_posting', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'),
        ForeignKeyConstraint(['posting_movement_id', 'posting_transaction_id'],
            ['inventory_movements.id', 'inventory_movements.transaction_id'],
            name='fk_scrap_line_movement_posting', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'),
        ForeignKeyConstraint(['root_disposition_id', 'loss_line_id', 'frozen_account_id', 'quantity'],
            ['stock_loss_dispositions.id', 'stock_loss_dispositions.line_id',
             'stock_loss_dispositions.source_account_id', 'stock_loss_dispositions.quantity'],
            name='fk_scrap_line_exact_root', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'),
        ForeignKeyConstraint(['root_disposition_id', 'original_decision_id', 'operation_type',
                              'operation_id', 'posting_transaction_id', 'posting_movement_id'],
            ['stock_loss_dispositions.id', 'stock_loss_dispositions.headquarters_decision_id',
             'stock_loss_dispositions.disposition', 'stock_loss_dispositions.scrap_operation_id',
             'stock_loss_dispositions.posting_transaction_id', 'stock_loss_dispositions.posting_movement_id'],
            name='fk_scrap_line_exact_original', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'),
        ForeignKeyConstraint(['correction_execution_id', 'root_disposition_id',
                              'predecessor_reversal_id', 'correction_decision_id', 'operation_id',
                              'posting_transaction_id', 'posting_movement_id', 'operation_type',
                              'frozen_account_id', 'quantity'],
            ['stock_loss_correction_executions.id', 'stock_loss_correction_executions.root_disposition_id',
             'stock_loss_correction_executions.reversal_id', 'stock_loss_correction_executions.correction_decision_id',
             'stock_loss_correction_executions.scrap_operation_id',
             'stock_loss_correction_executions.posting_transaction_id', 'stock_loss_correction_executions.posting_movement_id',
             'stock_loss_correction_executions.disposition', 'stock_loss_correction_executions.source_account_id',
             'stock_loss_correction_executions.quantity'],
            name='fk_scrap_line_exact_correction', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'),
        Column('source_hash', String(64), nullable=False),
        Column('plan_hash', String(64), nullable=False),
        Column('plan_jsonb', JSON_DOCUMENT, nullable=False),
        UniqueConstraint('operation_id', name='uq_scrap_line_order'),
        UniqueConstraint('original_decision_id', name='uq_scrap_line_original'),
        UniqueConstraint('correction_decision_id', name='uq_scrap_line_decision'),
        UniqueConstraint('correction_execution_id', name='uq_scrap_line_execution'),
        UniqueConstraint('posting_transaction_id', name='uq_scrap_line_transaction'),
        UniqueConstraint('posting_movement_id', name='uq_scrap_line_movement'),
        UniqueConstraint('id', 'root_disposition_id', name='uq_scrap_line_root'),
        UniqueConstraint('operation_id', 'root_disposition_id', 'posting_transaction_id',
                         'posting_movement_id', name='uq_scrap_line_original_binding'),
        UniqueConstraint('correction_execution_id', 'operation_id', 'root_disposition_id',
                         'posting_transaction_id', 'posting_movement_id', name='uq_scrap_line_correction_binding'),
        UniqueConstraint('id', 'root_disposition_id', 'posting_transaction_id', 'posting_movement_id',
                         'frozen_account_id', 'quantity', name='uq_scrap_line_recovery_stock'),
        UniqueConstraint('id', 'source_kind', name='uq_scrap_line_recovery_source'),
        UniqueConstraint('id', 'correction_execution_id', name='uq_scrap_line_recovery_correction'),
        CheckConstraint("operation_type='scrap' AND quantity>0 AND length(source_hash)=64 AND length(plan_hash)=64",
            name='ck_scrap_line_context'),
        CheckConstraint("(source_kind='original' AND original_decision_id IS NOT NULL "
            "AND correction_decision_id IS NULL AND predecessor_reversal_id IS NULL AND correction_execution_id IS NULL) OR "
            "(source_kind='correction' AND original_decision_id IS NULL AND correction_decision_id IS NOT NULL "
            "AND predecessor_reversal_id IS NOT NULL AND correction_execution_id IS NOT NULL)",
            name='ck_scrap_line_source'))

    serial = Table('stock_scrap_serials', metadata,
        identifier('scrap_line_id', 'stock_scrap_lines.id', primary=True),
        identifier('serial_id', 'inventory_serials.id', primary=True),
        identifier('previous_movement_id', 'inventory_movements.id'),
        identifier('admission_movement_id', 'inventory_movements.id'),
        Column('created_at', DateTime(timezone=True), nullable=False))
    files = evidence_table(metadata, 'stock_scrap_files', 'scrap_line_id', 'stock_scrap_lines')

    request = Table('stock_scrap_recovery_requests', metadata,
        *context('scrap_recovery_request'),
        identifier('scrap_line_id', 'stock_scrap_lines.id'),
        Column('expected_scrap_request_hash', String(64), nullable=False),
        UniqueConstraint('id', 'scrap_line_id', name='uq_scrap_recovery_request_line'),
        CheckConstraint('length(expected_scrap_request_hash)=64', name='ck_scrap_recovery_original_hash'))
    recovery_files = evidence_table(metadata, 'stock_scrap_recovery_files',
        'recovery_request_id', 'stock_scrap_recovery_requests')

    region = Table('stock_scrap_recovery_regional_reviews', metadata,
        *context('scrap_recovery_region'),
        identifier('recovery_request_id'), identifier('scrap_line_id'),
        ForeignKeyConstraint(['recovery_request_id', 'scrap_line_id'],
            ['stock_scrap_recovery_requests.id', 'stock_scrap_recovery_requests.scrap_line_id'],
            name='fk_scrap_recovery_region_request', ondelete='RESTRICT'),
        Column('expected_request_hash', String(64), nullable=False),
        Column('decision', String(32), nullable=False),
        UniqueConstraint('id', 'recovery_request_id', 'scrap_line_id', 'decision', name='uq_scrap_recovery_region_binding'),
        CheckConstraint("decision IN ('verified','needs_evidence') AND length(expected_request_hash)=64",
            name='ck_scrap_recovery_region_decision'))

    headquarters = Table('stock_scrap_recovery_headquarters_reviews', metadata,
        *context('scrap_recovery_hq'),
        identifier('regional_review_id'), identifier('recovery_request_id'), identifier('scrap_line_id'),
        Column('regional_decision', String(32), nullable=False),
        ForeignKeyConstraint(['regional_review_id', 'recovery_request_id', 'scrap_line_id', 'regional_decision'],
            ['stock_scrap_recovery_regional_reviews.id', 'stock_scrap_recovery_regional_reviews.recovery_request_id',
             'stock_scrap_recovery_regional_reviews.scrap_line_id', 'stock_scrap_recovery_regional_reviews.decision'],
            name='fk_scrap_recovery_hq_verified_region', ondelete='RESTRICT'),
        Column('expected_regional_hash', String(64), nullable=False),
        Column('decision', String(32), nullable=False),
        UniqueConstraint('regional_review_id', name='uq_scrap_recovery_hq_region'),
        UniqueConstraint('id', 'recovery_request_id', 'scrap_line_id', 'decision', name='uq_scrap_recovery_hq_binding'),
        CheckConstraint("regional_decision='verified' AND decision IN ('approve','request_regional_review') "
            "AND length(expected_regional_hash)=64", name='ck_scrap_recovery_hq_decision'))

    recovery = Table('stock_scrap_recovery_executions', metadata,
        *context('scrap_recovery_execution'),
        identifier('headquarters_review_id'), identifier('recovery_request_id'), identifier('scrap_line_id'),
        Column('headquarters_decision', String(32), nullable=False),
        ForeignKeyConstraint(['headquarters_review_id', 'recovery_request_id', 'scrap_line_id', 'headquarters_decision'],
            ['stock_scrap_recovery_headquarters_reviews.id', 'stock_scrap_recovery_headquarters_reviews.recovery_request_id',
             'stock_scrap_recovery_headquarters_reviews.scrap_line_id', 'stock_scrap_recovery_headquarters_reviews.decision'],
            name='fk_scrap_recovery_execution_hq', ondelete='RESTRICT'),
        identifier('reversal_id', 'stock_loss_disposition_reversals.id', deferred=True),
        ForeignKeyConstraint(['reversal_id', 'id', 'scrap_line_id'],
            ['stock_loss_disposition_reversals.id', 'stock_loss_disposition_reversals.scrap_recovery_execution_id',
             'stock_loss_disposition_reversals.scrap_line_id'],
            name='fk_scrap_recovery_exact_inverse', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'),
        Column('expected_headquarters_hash', String(64), nullable=False),
        Column('plan_hash', String(64), nullable=False),
        Column('plan_jsonb', JSON_DOCUMENT, nullable=False),
        UniqueConstraint('scrap_line_id', name='uq_scrap_recovery_execution_line'),
        UniqueConstraint('headquarters_review_id', name='uq_scrap_recovery_execution_review'),
        UniqueConstraint('reversal_id', name='uq_scrap_recovery_execution_inverse'),
        UniqueConstraint('id', 'reversal_id', 'scrap_line_id', name='uq_scrap_recovery_inverse_binding'),
        CheckConstraint("headquarters_decision='approve' AND length(expected_headquarters_hash)=64 AND length(plan_hash)=64",
            name='ck_scrap_recovery_execution_approval'))
    return (line, serial, files, request, recovery_files, region, headquarters, recovery)


def extend_parents(metadata, *, allow_base=False):
    """Desired model only; the migrator must prove old catalog/rows separately."""
    from app.database import Base
    if (metadata is Base.metadata and not allow_base) or 'stock_scrap_recovery_executions' not in metadata.tables:
        raise ValueError('explicit complete candidate metadata required')

    def replace_check(table, name, expression):
        matches = [c for c in table.constraints if isinstance(c, CheckConstraint) and c.name == name]
        if len(matches) != 1:
            raise ValueError('exact existing parent constraint required: ' + name)
        table.constraints.remove(matches[0])
        table.append_constraint(CheckConstraint(expression, name=name))

    order = metadata.tables['stock_operation_orders']
    order.append_constraint(UniqueConstraint('id', 'posting_transaction_id', name='uq_stock_operation_order_posting_id'))
    order.append_column(identifier('loss_correction_decision_id', 'stock_loss_correction_decisions.id', nullable=True))
    order.append_constraint(UniqueConstraint('loss_correction_decision_id', name='uq_stock_operation_orders_correction_decision'))
    replace_check(order, 'ck_stock_operation_orders_type_status',
        "(operation_type IN ('return','loss_report') AND status='submitted') OR (operation_type='scrap' AND status='posted')")
    replace_check(order, 'ck_stock_operation_orders_locations',
        "(operation_type='return' AND loss_correction_decision_id IS NULL AND ("
        "(oam_work_order_id IS NOT NULL AND loss_headquarters_decision_id IS NULL) OR "
        "(oam_work_order_id IS NULL AND loss_headquarters_decision_id IS NOT NULL)) "
        "AND target_location_id IS NOT NULL AND transit_location_id IS NOT NULL AND target_custody_assignment_id IS NOT NULL "
        "AND source_location_id<>target_location_id AND source_location_id<>transit_location_id AND target_location_id<>transit_location_id) OR "
        "(operation_type='loss_report' AND oam_work_order_id IS NULL AND loss_headquarters_decision_id IS NULL "
        "AND loss_correction_decision_id IS NULL AND target_location_id IS NULL AND transit_location_id IS NULL "
        "AND target_custody_assignment_id IS NULL) OR "
        "(operation_type='scrap' AND oam_work_order_id IS NULL AND target_location_id IS NULL AND transit_location_id IS NULL "
        "AND target_custody_assignment_id IS NULL AND ((loss_headquarters_decision_id IS NOT NULL AND loss_correction_decision_id IS NULL) "
        "OR (loss_headquarters_decision_id IS NULL AND loss_correction_decision_id IS NOT NULL)))")

    root = metadata.tables['stock_loss_dispositions']
    root.append_column(identifier('scrap_operation_id', 'stock_operation_orders.id', nullable=True, deferred=True))
    root.append_constraint(UniqueConstraint('scrap_operation_id', name='uq_loss_disposition_scrap_operation'))
    root.append_constraint(UniqueConstraint('id', 'line_id', 'source_account_id', 'quantity',
        name='uq_loss_disposition_scrap_root'))
    root.append_constraint(UniqueConstraint('id', 'headquarters_decision_id', 'disposition',
        'scrap_operation_id', 'posting_transaction_id', 'posting_movement_id', name='uq_loss_disposition_scrap_original'))
    root.append_constraint(ForeignKeyConstraint(
        ['scrap_operation_id', 'id', 'posting_transaction_id', 'posting_movement_id'],
        ['stock_scrap_lines.operation_id', 'stock_scrap_lines.root_disposition_id',
         'stock_scrap_lines.posting_transaction_id', 'stock_scrap_lines.posting_movement_id'],
        name='fk_loss_disposition_scrap_binding', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'))
    root.c.target_account_id.nullable = True
    replace_check(root, 'ck_loss_disposition_kind',
        "(disposition IN ('restore_available','convert_used','convert_damaged') AND return_operation_id IS NULL AND scrap_operation_id IS NULL) OR "
        "(disposition='return_to_region' AND return_operation_id IS NOT NULL AND return_operation_id<>operation_id AND scrap_operation_id IS NULL) OR "
        "(disposition='scrap' AND scrap_operation_id IS NOT NULL AND scrap_operation_id<>operation_id AND return_operation_id IS NULL)")
    replace_check(root, 'ck_loss_disposition_dimensions',
        "quantity>0 AND authorization_version>0 AND ((disposition='scrap' AND target_account_id IS NULL) OR "
        "(disposition<>'scrap' AND target_account_id IS NOT NULL AND source_account_id<>target_account_id))")

    correction = metadata.tables['stock_loss_correction_executions']
    correction.append_column(identifier('scrap_operation_id', 'stock_operation_orders.id', nullable=True, deferred=True))
    correction.append_constraint(UniqueConstraint('scrap_operation_id', name='uq_loss_correction_execution_scrap'))
    correction.append_constraint(UniqueConstraint('id', 'root_disposition_id', 'reversal_id',
        'correction_decision_id', 'scrap_operation_id', 'posting_transaction_id', 'posting_movement_id',
        'disposition', 'source_account_id', 'quantity',
        name='uq_loss_correction_scrap_binding'))
    correction.append_constraint(ForeignKeyConstraint(
        ['id', 'scrap_operation_id', 'root_disposition_id', 'posting_transaction_id', 'posting_movement_id'],
        ['stock_scrap_lines.correction_execution_id', 'stock_scrap_lines.operation_id',
         'stock_scrap_lines.root_disposition_id', 'stock_scrap_lines.posting_transaction_id',
         'stock_scrap_lines.posting_movement_id'],
        name='fk_loss_correction_scrap_child', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'))
    correction.append_constraint(CheckConstraint(
        "(disposition='scrap' AND scrap_operation_id IS NOT NULL) OR (disposition<>'scrap' AND scrap_operation_id IS NULL)",
        name='ck_loss_correction_execution_scrap'))

    inverse = metadata.tables['stock_loss_disposition_reversals']
    inverse.append_column(identifier('scrap_recovery_execution_id', 'stock_scrap_recovery_executions.id',
        nullable=True, deferred=True))
    inverse.append_column(identifier('scrap_line_id', 'stock_scrap_lines.id', nullable=True, deferred=True))
    inverse.append_column(Column('scrap_source_kind', String(24), nullable=True))
    inverse.append_constraint(UniqueConstraint('scrap_recovery_execution_id', name='uq_loss_inverse_scrap_recovery'))
    inverse.append_constraint(UniqueConstraint('scrap_line_id', name='uq_loss_inverse_scrap_line'))
    inverse.append_constraint(UniqueConstraint('id', 'scrap_recovery_execution_id', 'scrap_line_id',
        name='uq_loss_inverse_scrap_binding'))
    inverse.append_constraint(ForeignKeyConstraint(
        ['scrap_recovery_execution_id', 'id', 'scrap_line_id'],
        ['stock_scrap_recovery_executions.id', 'stock_scrap_recovery_executions.reversal_id',
         'stock_scrap_recovery_executions.scrap_line_id'],
        name='fk_loss_inverse_scrap_recovery', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'))
    inverse.append_constraint(ForeignKeyConstraint(
        ['scrap_line_id', 'root_disposition_id', 'original_transaction_id', 'original_movement_id',
         'target_account_id', 'quantity'],
        ['stock_scrap_lines.id', 'stock_scrap_lines.root_disposition_id', 'stock_scrap_lines.posting_transaction_id',
         'stock_scrap_lines.posting_movement_id', 'stock_scrap_lines.frozen_account_id', 'stock_scrap_lines.quantity'],
        name='fk_loss_inverse_scrap_stock', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'))
    inverse.append_constraint(ForeignKeyConstraint(['scrap_line_id', 'scrap_source_kind'],
        ['stock_scrap_lines.id', 'stock_scrap_lines.source_kind'],
        name='fk_loss_inverse_scrap_source', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'))
    inverse.append_constraint(ForeignKeyConstraint(['scrap_line_id', 'reversed_correction_id'],
        ['stock_scrap_lines.id', 'stock_scrap_lines.correction_execution_id'],
        name='fk_loss_inverse_scrap_correction', ondelete='RESTRICT', deferrable=True, initially='DEFERRED'))
    inverse.append_constraint(CheckConstraint(
        "(source_account_id IS NULL AND scrap_recovery_execution_id IS NOT NULL AND scrap_line_id IS NOT NULL "
        "AND scrap_source_kind IS NOT NULL AND ((scrap_source_kind='original' AND reversed_correction_id IS NULL) "
        "OR (scrap_source_kind='correction' AND reversed_correction_id IS NOT NULL))) OR "
        "(source_account_id IS NOT NULL AND scrap_recovery_execution_id IS NULL AND scrap_line_id IS NULL "
        "AND scrap_source_kind IS NULL)", name='ck_loss_inverse_scrap_recovery'))
    return (order, root, correction, inverse)


def build_schema():
    """Complete isolated model; formal Base activation is explicit."""
    from app.stock_scrap_schema import build_schema as complete_schema
    return complete_schema()
