"""Relational identity tests; external parents are explicit minimal stubs.

Not an approval, permission, migration, posting or COMMIT business proof. The
owned native gate separately applies the DDL over real 0164 historical facts.
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Column, ForeignKeyConstraint, MetaData, Table, UniqueConstraint, create_engine, event
from sqlalchemy.exc import IntegrityError
from pglast import parse_sql
from app.database import Base
from app.stock_scrap_persistence_schema import build_schema, define
from app.stock_scrap_schema import predecessor_schema
from pg16_stock_scrap_schema import compile_structure


def test_compiler_installs_parent_uniqueness_before_all_new_foreign_keys():
    old_tables = set(Base.metadata.tables)
    old_columns = {name: tuple(table.columns.keys()) for name, table in Base.metadata.tables.items()}
    metadata, tables, parents, statements = compile_structure()
    for statement in statements:
        parse_sql(statement)
    assert len(tables) == 8 and len(parents) == 4
    first_fk = next(i for i,s in enumerate(statements) if ' FOREIGN KEY' in s)
    assert all(' FOREIGN KEY' in s for s in statements[first_fk:])
    assert sum(' ADD COLUMN ' in s for s in statements) == 6
    assert not any('GRANT ' in s or 'DISABLE TRIGGER' in s for s in statements)
    assert set(Base.metadata.tables) == old_tables
    assert {name: tuple(table.columns.keys()) for name, table in Base.metadata.tables.items()} == old_columns
    with pytest.raises(ValueError, match='live application metadata'):
        define(Base.metadata)
    for table in parents:
        before = predecessor_schema().tables[table.name]
        for fk in before.foreign_key_constraints:
            assert any(tuple(c.name for c in candidate.columns) == tuple(c.name for c in fk.columns)
                and tuple(e.target_fullname for e in candidate.elements) == tuple(e.target_fullname for e in fk.elements)
                for candidate in table.foreign_key_constraints)


@pytest.fixture(params=['original','correction'])
def database(request):
    metadata, tables, _ = build_schema()
    dependencies = {}
    reference_keys = {}
    for table in tables:
        for fk in table.foreign_key_constraints:
            target = next(iter(fk.elements)).column.table
            if target in tables:
                continue
            names = tuple(element.column.name for element in fk.elements)
            reference_keys.setdefault(target.name, set()).add(names)
            dependencies.setdefault(target.name, {}).update({element.column.name: element.column for element in fk.elements})
    stubs = MetaData()
    identities = {name:uuid4() for name in dependencies}
    identities['users'] = str(identities['users'])
    spares = {name:uuid4() for name in dependencies}
    spares['users'] = str(spares['users'])
    stub_tables = {}
    for name, columns in dependencies.items():
        stub_tables[name] = Table(name, stubs,
            *(Column(key, col.type, primary_key=key=='id', nullable=col.nullable) for key,col in columns.items()),
            *(UniqueConstraint(*key) for key in sorted(reference_keys[name]) if key != ('id',)))
    # Match the exact parent dimensions while retaining the root's old ordinary
    # disposition in correction mode. That older root must not be rewritten.
    shared = dict(line_id=identities['stock_operation_lines'], source_account_id=identities['stock_accounts'],
        quantity=Decimal('1.000'), headquarters_decision_id=identities['stock_loss_headquarters_decisions'],
        disposition='scrap', scrap_operation_id=identities['stock_operation_orders'],
        posting_transaction_id=identities['inventory_transactions'], posting_movement_id=identities['inventory_movements'],
        operation_type='scrap', transaction_id=identities['inventory_transactions'],
        root_disposition_id=identities['stock_loss_dispositions'], reversal_id=identities['stock_loss_disposition_reversals'],
        correction_decision_id=identities['stock_loss_correction_decisions'],
        scrap_recovery_execution_id=None, scrap_line_id=None)
    engine=create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def enforce_foreign_keys(dbapi, _):
        dbapi.execute('PRAGMA foreign_keys=ON')
    stubs.create_all(engine)
    metadata.create_all(engine,tables=tables)
    with engine.begin() as db:
        for name,table in stub_tables.items():
            values={column.name:shared[column.name] for column in table.columns if column.name!='id'}
            if name=='stock_loss_dispositions' and request.param=='correction':
                values.update(disposition='restore_available', scrap_operation_id=None)
            db.execute(table.insert(), dict(id=identities[name],**values))
            db.execute(table.insert(), dict(id=spares[name],**values))
    line=dict(id=uuid4(),created_at=datetime.now(timezone.utc),operation_id=identities['stock_operation_orders'],
        operation_type='scrap',loss_line_id=identities['stock_operation_lines'],
        root_disposition_id=identities['stock_loss_dispositions'],source_kind=request.param,
        original_decision_id=identities['stock_loss_headquarters_decisions'] if request.param=='original' else None,
        correction_decision_id=identities['stock_loss_correction_decisions'] if request.param=='correction' else None,
        predecessor_reversal_id=identities['stock_loss_disposition_reversals'] if request.param=='correction' else None,
        correction_execution_id=identities['stock_loss_correction_executions'] if request.param=='correction' else None,
        frozen_account_id=identities['stock_accounts'],custody_assignment_id=identities['custody_assignments'],
        quantity=Decimal('1.000'),posting_transaction_id=identities['inventory_transactions'],
        posting_movement_id=identities['inventory_movements'],source_hash='a'*64,plan_hash='b'*64,plan_jsonb={})
    with engine.connect() as db:
        yield db,metadata.tables['stock_scrap_lines'],line,spares,request.param
    engine.dispose()


def test_exact_original_and_corrected_bindings_accept_their_own_parents(database):
    db,table,line,_,_=database
    db.execute(table.insert(),line)
    db.commit()  # Deferred composite keys must be checked, not just flushed.


def rollback_sqlite_failed_commit(db):
    # SQLite retains the transaction after a deferred-FK COMMIT failure while
    # SQLAlchemy has ended its wrapper. Roll back the test DBAPI transaction as
    # well; PostgreSQL aborts this failed commit and is verified separately.
    db.connection.rollback()
    db.rollback()


@pytest.mark.parametrize('field,target',[
    ('loss_line_id','stock_operation_lines'),('frozen_account_id','stock_accounts'),
    ('posting_transaction_id','inventory_transactions'),('posting_movement_id','inventory_movements'),
    ('operation_id','stock_operation_orders'),
])
def test_existing_but_unrelated_references_cannot_replace_exact_parent_binding(database,field,target):
    db,table,line,spares,_=database
    with pytest.raises(IntegrityError):
        db.execute(table.insert(),line|{field:spares[target]})
        db.commit()
    rollback_sqlite_failed_commit(db)
    assert db.execute(table.select()).all()==[]


def test_positive_quantity_cannot_exceed_the_bound_original_share(database):
    db,table,line,_,_=database
    with pytest.raises(IntegrityError):
        db.execute(table.insert(),line|{'quantity':Decimal('2.000')})
        db.commit()
    rollback_sqlite_failed_commit(db)


def test_source_specific_approved_identity_cannot_be_substituted(database):
    db,table,line,spares,kind=database
    field,target=('original_decision_id','stock_loss_headquarters_decisions') if kind=='original' else ('correction_decision_id','stock_loss_correction_decisions')
    with pytest.raises(IntegrityError):
        db.execute(table.insert(),line|{field:spares[target]})
        db.commit()
    rollback_sqlite_failed_commit(db)
