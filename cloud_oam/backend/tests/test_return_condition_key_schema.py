"""Isolated structure and the PostgreSQL-only product registrar boundary."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app.return_condition_key_schema import ALIASES, NAME, build_schema
from app.formal_services.stock_loss_corrections import return_condition_keys as keys


def test_registry_is_isolated_and_registrar_has_no_sqlite_product_fallback():
    metadata,tables,_=build_schema()
    assert NAME in Base.metadata.tables and metadata is not Base.metadata and len(tables)==6
    assert set(ALIASES)<set(metadata.tables[NAME].c.keys())
    engine=create_engine('sqlite+pysqlite:///:memory:')
    try:
        with Session(engine) as db, pytest.raises(RuntimeError,match='requires PostgreSQL'):
            keys.record(db,event={},request=None)
    finally:
        engine.dispose()
