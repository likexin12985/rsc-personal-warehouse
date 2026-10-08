"""Native bidirectional structural proof. No lifecycle or write-service claim."""
from copy import deepcopy
import hashlib
import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from stock_scrap_relational_fixture import fixture_schema, scenario, insert_scrap, insert_reviews, insert_recovery


def snapshot(owner, metadata):
    with owner.connect() as db:
        data = {name: sorted(json.dumps(dict(row), sort_keys=True, default=str)
            for row in db.execute(select(table)).mappings()) for name, table in metadata.tables.items()}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def run(owner, *, kind):
    metadata = fixture_schema()
    metadata.create_all(owner)
    data = scenario(metadata, kind)
    rejected = []

    def reject(name, action, *, sqlstate='23503', at_commit=True):
        before = snapshot(owner, metadata)
        inserted = False
        try:
            with owner.begin() as db:
                action(db)
                inserted = True
        except DBAPIError as error:
            assert error.orig.sqlstate == sqlstate, (name, str(error))
            assert inserted == at_commit, (name, 'wrong failure phase')
            assert snapshot(owner, metadata) == before, name + ': rollback altered history'
            rejected.append(dict(case=name, phase='commit' if inserted else 'statement',
                sqlstate=sqlstate, constraint=error.orig.diag.constraint_name, allRowsPreserved=True))
        else:
            raise AssertionError(name + ': invalid structure committed')

    reject('scrap_parent_without_child', lambda db: insert_scrap(db, metadata, data, include_line=False))
    with owner.begin() as db:
        insert_scrap(db, metadata, data)
        insert_reviews(db, metadata, data)
    for only in ('inverse', 'recovery'):
        reject('orphan_' + only, lambda db: insert_recovery(db, metadata, data, order=(only,)))
    substitutions = dict(root_disposition_id=data['spares']['stock_loss_dispositions'],
        original_transaction_id=data['alternate_transaction_id'], original_movement_id=data['alternate_movement_id'],
        target_account_id=data['spares']['stock_accounts'], quantity=Decimal('2.000'))
    for field, value in substitutions.items():
        changed = deepcopy(data)
        changed['inverse'][field] = value
        reject('inverse_' + field, lambda db: insert_recovery(db, metadata, changed))
    changed = deepcopy(data)
    changed['recovery']['reversal_id'] = data['ids']['stock_loss_disposition_reversals']
    reject('recovery_points_to_another_inverse', lambda db: insert_recovery(db, metadata, changed))
    for field in ('scrap_line_id', 'scrap_source_kind', 'scrap_recovery_execution_id'):
        changed = deepcopy(data)
        changed['inverse'][field] = None
        reject('null_' + field, lambda db: insert_recovery(db, metadata, changed), sqlstate='23514', at_commit=False)
    changed = deepcopy(data)
    changed['inverse']['reversed_correction_id'] = data['spares']['stock_loss_correction_executions']
    reject('another_correction', lambda db: insert_recovery(db, metadata, changed),
        sqlstate='23503' if kind == 'correction' else '23514', at_commit=kind == 'correction')
    changed = deepcopy(data)
    changed['inverse'].update(scrap_source_kind='original' if kind == 'correction' else 'correction',
        reversed_correction_id=None if kind == 'correction' else data['ids']['stock_loss_correction_executions'])
    reject('source_kind_swap', lambda db: insert_recovery(db, metadata, changed))

    with owner.begin() as db:
        insert_recovery(db, metadata, data)
    # An independent source tests recovery-before-inverse insertion as well.
    second = scenario(metadata, kind)
    with owner.begin() as db:
        insert_scrap(db, metadata, second)
        insert_reviews(db, metadata, second)
        insert_recovery(db, metadata, second, order=('recovery', 'inverse'))
    return dict(passed=True, sourceKind=kind, bothInsertionOrdersCommitted=True,
        rejected=rejected, externalParents='minimal stubs with exact new reciprocal constraints',
        historicalBusinessProof=False, postingImplemented=False)
