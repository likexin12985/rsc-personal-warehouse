"""Attempt orphan and cross-wired scrap/recovery commits, both source kinds."""
from copy import deepcopy
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.exc import IntegrityError
from stock_scrap_relational_fixture import fixture_schema, scenario, insert_scrap, insert_reviews, insert_recovery
from test_stock_scrap_persistence_schema import rollback_sqlite_failed_commit


@pytest.fixture(params=['original', 'correction'])
def case(request):
    metadata = fixture_schema()
    engine = create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def fk(dbapi, _):
        dbapi.execute('PRAGMA foreign_keys=ON')
    metadata.create_all(engine)
    with engine.connect() as db:
        yield db, metadata, scenario(metadata, request.param)
    engine.dispose()


@pytest.mark.parametrize('order', [('inverse', 'recovery'), ('recovery', 'inverse')])
def test_complete_scrap_and_recovery_pair_commits_in_either_order(case, order):
    db, metadata, data = case
    insert_scrap(db, metadata, data)
    db.commit()
    insert_reviews(db, metadata, data)
    db.commit()
    insert_recovery(db, metadata, data, order=order)
    db.commit()
    assert db.scalar(select(func.count()).select_from(metadata.tables['stock_scrap_recovery_executions'])) == 1


def test_original_or_correction_parent_requires_its_scrap_child_at_commit(case):
    db, metadata, data = case
    insert_scrap(db, metadata, data, include_line=False)
    with pytest.raises(IntegrityError):
        db.commit()
    rollback_sqlite_failed_commit(db)
    assert db.scalar(select(func.count()).select_from(metadata.tables['stock_loss_dispositions'])) == 0


@pytest.mark.parametrize('only', ['inverse', 'recovery'])
def test_neither_half_of_recovery_can_commit_alone(case, only):
    db, metadata, data = case
    insert_scrap(db, metadata, data)
    insert_reviews(db, metadata, data)
    db.commit()
    insert_recovery(db, metadata, data, order=(only,))
    with pytest.raises(IntegrityError):
        db.commit()
    rollback_sqlite_failed_commit(db)
    assert db.scalar(select(func.count()).select_from(metadata.tables['stock_scrap_recovery_executions'])) == 0


MISMATCHES = ('root_disposition_id', 'original_transaction_id', 'original_movement_id',
              'target_account_id', 'quantity', 'recovery_reversal_id')


def mismatch(data, field):
    changed = deepcopy(data)
    if field == 'recovery_reversal_id':
        changed['recovery']['reversal_id'] = data['ids']['stock_loss_disposition_reversals']
    else:
        value = {
            'root_disposition_id': data['spares']['stock_loss_dispositions'],
            'original_transaction_id': data['alternate_transaction_id'],
            'original_movement_id': data['alternate_movement_id'],
            'target_account_id': data['spares']['stock_accounts'],
            'quantity': Decimal('2.000'),
        }[field]
        changed['inverse'][field] = value
    return changed


@pytest.mark.parametrize('field', MISMATCHES)
def test_found_stock_cannot_restore_a_different_existing_source(case, field):
    db, metadata, data = case
    insert_scrap(db, metadata, data)
    insert_reviews(db, metadata, data)
    db.commit()
    insert_recovery(db, metadata, mismatch(data, field))
    with pytest.raises(IntegrityError):
        db.commit()
    rollback_sqlite_failed_commit(db)
    assert db.scalar(select(func.count()).select_from(metadata.tables['stock_scrap_recovery_executions'])) == 0


@pytest.mark.parametrize('field', ['scrap_line_id', 'scrap_source_kind', 'scrap_recovery_execution_id'])
def test_null_cannot_disable_scrap_recovery_binding(case, field):
    db, metadata, data = case
    insert_scrap(db, metadata, data)
    insert_reviews(db, metadata, data)
    db.commit()
    data['inverse'][field] = None
    with pytest.raises(IntegrityError):
        insert_recovery(db, metadata, data)
        db.commit()
    rollback_sqlite_failed_commit(db)


def test_recovery_cannot_claim_another_existing_correction(case):
    db, metadata, data = case
    insert_scrap(db, metadata, data)
    insert_reviews(db, metadata, data)
    db.commit()
    data['inverse']['reversed_correction_id'] = data['spares']['stock_loss_correction_executions']
    with pytest.raises(IntegrityError):
        insert_recovery(db, metadata, data)
        db.commit()
    rollback_sqlite_failed_commit(db)
