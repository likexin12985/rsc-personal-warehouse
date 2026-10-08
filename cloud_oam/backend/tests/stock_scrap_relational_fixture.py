"""Exact new constraints with minimal external parents, for SQLite and PG16.

This deliberately excludes old business triggers. It proves relational
bindings, not approvals, posting authority, migrations, or lifecycle changes.
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Column, MetaData, Table, UniqueConstraint, CheckConstraint, ForeignKeyConstraint
from app.stock_scrap_persistence_schema import build_schema


def fixture_schema():
    full, facts, parents = build_schema()
    fact_names = {t.name for t in facts}
    constraints = []
    for parent in parents:
        constraints.extend(c for c in parent.constraints if c.name and (
            isinstance(c, ForeignKeyConstraint) and c.name.startswith('fk_loss_') and 'scrap' in c.name
            or isinstance(c, CheckConstraint) and c.name in (
                'ck_loss_inverse_scrap_recovery', 'ck_loss_correction_execution_scrap')))
    columns, keys = {}, {}
    for constraint in [c for table in facts for c in table.foreign_key_constraints] + constraints:
        if constraint.table.name not in fact_names:
            names = (['source_account_id', 'scrap_recovery_execution_id', 'scrap_line_id',
                      'scrap_source_kind', 'reversed_correction_id']
                     if constraint.name == 'ck_loss_inverse_scrap_recovery' else
                     ['disposition', 'scrap_operation_id']
                     if constraint.name == 'ck_loss_correction_execution_scrap' else
                     list(constraint.columns.keys()))
            columns.setdefault(constraint.table.name, set()).update(['id', *names])
        if isinstance(constraint, ForeignKeyConstraint):
            target = next(iter(constraint.elements)).column.table.name
            names = tuple(e.column.name for e in constraint.elements)
            if target not in fact_names:
                columns.setdefault(target, set()).update(['id', *names])
                keys.setdefault(target, set()).add(names)
    metadata = MetaData()
    for name, names in columns.items():
        source = full.tables[name]
        Table(name, metadata,
            *(Column(n, source.c[n].type, nullable=source.c[n].nullable, primary_key=n == 'id')
              for n in sorted(names)),
            *(UniqueConstraint(*k) for k in sorted(keys.get(name, ())) if k != ('id',)))
    for table in facts:
        table.to_metadata(metadata)
    for constraint in constraints:
        target = metadata.tables[constraint.table.name]
        if isinstance(constraint, ForeignKeyConstraint):
            target.append_constraint(ForeignKeyConstraint(list(constraint.columns.keys()),
                [e.target_fullname for e in constraint.elements], name=constraint.name,
                ondelete=constraint.ondelete, deferrable=constraint.deferrable, initially=constraint.initially))
        else:
            target.append_constraint(CheckConstraint(str(constraint.sqltext), name=constraint.name))
    return metadata


def scenario(metadata, kind):
    now = datetime.now(timezone.utc)
    ids = {name: uuid4() for name in metadata.tables}
    ids['users'] = str(ids['users'])
    spare = {name: uuid4() for name in metadata.tables}
    spare['users'] = str(spare['users'])
    original_tx, original_move, recovery_tx, recovery_move = (uuid4() for _ in range(4))
    root = ids['stock_loss_dispositions']
    correction = ids['stock_loss_correction_executions']
    line_id = ids['stock_scrap_lines']
    recovery_id = ids['stock_scrap_recovery_executions']
    inverse_id = spare['stock_loss_disposition_reversals']
    shared = dict(line_id=ids['stock_operation_lines'], source_account_id=ids['stock_accounts'],
        target_account_id=ids['stock_accounts'], quantity=Decimal('1.000'),
        headquarters_decision_id=ids['stock_loss_headquarters_decisions'], disposition='scrap',
        scrap_operation_id=ids['stock_operation_orders'], posting_transaction_id=original_tx,
        posting_movement_id=original_move, original_transaction_id=original_tx,
        original_movement_id=original_move, operation_type='scrap', transaction_id=original_tx,
        root_disposition_id=root, reversal_id=ids['stock_loss_disposition_reversals'],
        correction_decision_id=ids['stock_loss_correction_decisions'], scrap_recovery_execution_id=None,
        scrap_line_id=None, scrap_source_kind=None, reversed_correction_id=None)
    parents = []
    for name, table in metadata.tables.items():
        if name.startswith('stock_scrap_'):
            continue
        values = dict(id=ids[name], **{c.name: shared[c.name] for c in table.columns if c.name != 'id'})
        if name == 'stock_loss_dispositions' and kind == 'correction':
            values.update(disposition='restore_available', scrap_operation_id=None)
        if name == 'stock_loss_correction_executions' and kind == 'original':
            values.update(disposition='restore_available', scrap_operation_id=None)
        parents.append((name, values))
        if name in ('stock_accounts', 'stock_loss_dispositions', 'stock_loss_correction_executions'):
            other = values | {'id': spare[name]}
            if name != 'stock_accounts':
                other.update(disposition='restore_available', scrap_operation_id=None)
            parents.append((name, other))
        # Additional inventory facts permit wrong-but-existing original inputs.
        if name in ('inventory_transactions', 'inventory_movements'):
            parents.extend((name, values | {'id': identifier}) for identifier in (
                (original_tx, recovery_tx) if name == 'inventory_transactions' else (original_move, recovery_move)))
    line = dict(id=line_id, created_at=now, operation_id=ids['stock_operation_orders'], operation_type='scrap',
        loss_line_id=ids['stock_operation_lines'], root_disposition_id=root, source_kind=kind,
        original_decision_id=ids['stock_loss_headquarters_decisions'] if kind == 'original' else None,
        correction_decision_id=ids['stock_loss_correction_decisions'] if kind == 'correction' else None,
        predecessor_reversal_id=ids['stock_loss_disposition_reversals'] if kind == 'correction' else None,
        correction_execution_id=correction if kind == 'correction' else None,
        frozen_account_id=ids['stock_accounts'], custody_assignment_id=ids['custody_assignments'],
        quantity=Decimal('1.000'), posting_transaction_id=original_tx, posting_movement_id=original_move,
        source_hash='a'*64, plan_hash='b'*64, plan_jsonb={})

    def context(name):
        key = str(ids[name]).replace('-', '') * 2
        return dict(id=ids[name], created_at=now, actor_user_id=ids['users'], actor_person_id=ids['people'],
            authorization_version=1, request_id='request-' + key[:24], idempotency_key_hash=key,
            request_hash='a'*64, reason='Found exact reported material', command_jsonb={})

    request = context('stock_scrap_recovery_requests') | dict(scrap_line_id=line_id, expected_scrap_request_hash='a'*64)
    region = context('stock_scrap_recovery_regional_reviews') | dict(recovery_request_id=request['id'],
        scrap_line_id=line_id, expected_request_hash='a'*64, decision='verified')
    hq = context('stock_scrap_recovery_headquarters_reviews') | dict(regional_review_id=region['id'],
        recovery_request_id=request['id'], scrap_line_id=line_id, regional_decision='verified',
        expected_regional_hash='a'*64, decision='approve')
    recovery = context('stock_scrap_recovery_executions') | dict(headquarters_review_id=hq['id'],
        recovery_request_id=request['id'], scrap_line_id=line_id, headquarters_decision='approve',
        reversal_id=inverse_id, expected_headquarters_hash='a'*64, plan_hash='b'*64, plan_jsonb={})
    inverse_table = metadata.tables['stock_loss_disposition_reversals']
    inverse = {c.name: shared[c.name] for c in inverse_table.columns if c.name != 'id'}
    inverse.update(id=inverse_id, source_account_id=None, scrap_recovery_execution_id=recovery_id,
        scrap_line_id=line_id, scrap_source_kind=kind,
        reversed_correction_id=correction if kind == 'correction' else None)
    return dict(parents=parents, line=line, request=request, region=region, headquarters=hq,
        recovery=recovery, inverse=inverse, ids=ids, spares=spare,
        alternate_transaction_id=recovery_tx, alternate_movement_id=recovery_move)


def insert_scrap(db, metadata, data, *, include_line=True):
    for name, values in data['parents']:
        db.execute(metadata.tables[name].insert(), values)
    if include_line:
        db.execute(metadata.tables['stock_scrap_lines'].insert(), data['line'])


def insert_reviews(db, metadata, data):
    for key, name in (('request', 'stock_scrap_recovery_requests'),
                      ('region', 'stock_scrap_recovery_regional_reviews'),
                      ('headquarters', 'stock_scrap_recovery_headquarters_reviews')):
        db.execute(metadata.tables[name].insert(), data[key])


def insert_recovery(db, metadata, data, *, order=('inverse', 'recovery')):
    for key in order:
        name = 'stock_loss_disposition_reversals' if key == 'inverse' else 'stock_scrap_recovery_executions'
        db.execute(metadata.tables[name].insert(), data[key])
