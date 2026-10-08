"""Formal metadata and explicit historical views must remain distinct."""
from pathlib import Path
import runpy
import pytest
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects.sqlite import dialect
from app import models
from app.database import Base
from app import return_condition_application_schema as complete
from app.stock_scrap_schema import predecessor_schema as before_scrap
from app.return_condition_schema import build_schema as initial_schema


def shapes(metadata):
    # SQLAlchemy stores constraints in a set. Compare their exact fragments
    # independently of iteration order, retaining column order and literals.
    identity = runpy.run_path(str(Path(__file__).resolve().parents[1]/'alembic/return_condition_0167/sqlite_transition.py'))['table_identity']
    return {n:identity(str(CreateTable(t).compile(dialect=dialect()))) for n,t in metadata.tables.items()}


def test_all_ten_facts_share_current_metadata_and_existing_mappers_keep_their_tables():
    metadata,tables,parents=complete.build_schema()
    assert tuple(t.name for t in tables)==complete.TABLE_NAMES
    assert len(tables)==10 and len(parents)==4
    assert shapes(metadata)==shapes(Base.metadata)
    for mapper in Base.registry.mappers:
        if mapper.local_table.name in complete.PARENT_NAMES:
            assert mapper.local_table is Base.metadata.tables[mapper.local_table.name]
    # Resolve every foreign key, including both circular source bindings.
    for table in metadata.tables.values():
        for foreign_key in table.foreign_keys:
            assert foreign_key.column is not None


def test_old_schema_tools_get_real_predecessors_without_mutating_current_metadata():
    before=shapes(Base.metadata)
    old=complete.predecessor_schema()
    assert not set(complete.TABLE_NAMES).intersection(old.tables)
    for name in ('stock_operation_orders','stock_operation_lines'):
        assert 'condition_case_id' not in old.tables[name].c
    original=before_scrap()
    assert not set(complete.TABLE_NAMES).intersection(original.tables)
    assert 'stock_scrap_lines' not in original.tables
    for table in original.tables.values():
        for foreign_key in table.foreign_keys:
            assert foreign_key.column is not None
    _,initial,_=initial_schema()
    assert len(initial)==4
    assert shapes(Base.metadata)==before


def test_partial_or_bad_predecessor_never_changes_metadata():
    old=complete.predecessor_schema()
    table=old.tables['stock_operation_orders']
    target=next(c for c in table.constraints if c.name=='ck_stock_operation_orders_type_status')
    table.constraints.remove(target)
    before=shapes(old)
    with pytest.raises(ValueError,match='exact predecessor constraint'):
        complete.register(old)
    assert shapes(old)==before
    before=shapes(Base.metadata)
    with pytest.raises(ValueError,match='registered once'):
        complete.register(Base.metadata)
    assert shapes(Base.metadata)==before
