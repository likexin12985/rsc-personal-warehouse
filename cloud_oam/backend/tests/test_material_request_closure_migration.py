"""Closure tooling boundary and exact frozen migration/application schema."""
from pathlib import Path
import runpy

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

import app.models  # Register the complete production metadata.
from app.database import Base
from app.material_request_closure_schema import closures

FOLDER = Path(__file__).parents[1] / 'alembic/request_closure_0169'
SQLITE = runpy.run_path(str(FOLDER / 'sqlite_transition.py'))


def test_frozen_closure_matches_production_metadata():
    frozen = runpy.run_path(str(FOLDER / 'schema.py'))['closures']
    # SQLAlchemy CHECK constraint iteration order is not stable; compare each
    # compiled column/constraint separately, without dropping any expression.
    from sqlalchemy.schema import CreateColumn, AddConstraint
    dialect = postgresql.dialect()
    def shape(table):
        return ([str(CreateColumn(c).compile(dialect=dialect)) for c in table.columns],
                sorted(str(AddConstraint(c).compile(dialect=dialect)) for c in table.constraints))
    assert shape(frozen) == shape(closures) == shape(Base.metadata.tables['material_request_closures'])


def tooling():
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE alembic_version (version_num TEXT NOT NULL)')
        db.exec_driver_sql("INSERT INTO alembic_version VALUES ('20261217_0168')")
    return engine


def test_sqlite_schema_roundtrip_and_reject_business_insert():
    engine = tooling()
    with engine.begin() as db:
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261218_0169'")
        with pytest.raises(IntegrityError, match='closure writes require PostgreSQL16'):
            db.exec_driver_sql('INSERT INTO material_request_closures DEFAULT VALUES')
        SQLITE['transition'](db, up=False)
        assert not SQLITE['objects'](db)


def test_sqlite_refuses_unknown_predecessor_or_modified_guard():
    engine = tooling()
    with engine.begin() as db:
        db.exec_driver_sql("UPDATE alembic_version SET version_num='unknown'")
        with pytest.raises(ValueError, match='exact single SQLite predecessor'):
            SQLITE['transition'](db, up=True)
        assert not SQLITE['objects'](db)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261217_0168'")
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261218_0169'")
        db.exec_driver_sql('DROP TRIGGER rsc_closure_sqlite_insert_0169')
        with pytest.raises(ValueError, match='exact SQLite closure schema'):
            SQLITE['transition'](db, up=False)
        assert 'material_request_closures' in SQLITE['objects'](db)
