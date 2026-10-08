"""Formal 0171 frozen schema and schema-tooling refusal boundaries."""
from copy import copy
from pathlib import Path
import json
import runpy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateColumn, AddConstraint
from sqlalchemy.dialects import postgresql

import app.models
from app.database import Base
from app.material_request_remaining_cancel_schema import cancellations, cancellation_lines

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'alembic/remaining_cancel_0171'
SQLITE = runpy.run_path(str(FOLDER / 'sqlite_transition.py'))


def test_frozen_schema_matches_registered_metadata_and_forward_sources():
    from app import database_security as security
    from app.material_request_remaining_cancel_readiness import DATA as ready
    from app.material_request_remaining_cancel_security import DATA as runtime
    revision = runpy.run_path(str(ROOT / 'alembic/versions/20261220_0171_remaining_demand_cancellation.py'))
    assert revision['revision'] == '20261220_0171' and revision['down_revision'] == '20261219_0170'
    frozen = revision['_support']('transition')['DATA']
    from forward_catalog_expectations import current_catalog
    expected = current_catalog(revision['revision'], {k:v for k,v in frozen.items() if k != 'statements'})
    assert expected == runtime
    assert len(revision['_sources']()) == 3
    from migration_source_expectations import current_source_body
    assert security._stock_scrap_readiness.DATA['after']['prosrc'] == current_source_body('20261220_0171', 'public.'+ready['after']['signature'], ready['after']['prosrc'])
    dialect = postgresql.dialect()
    def shape(table):
        return ([str(CreateColumn(c).compile(dialect=dialect)) for c in table.columns],
                sorted(str(AddConstraint(copy(c)).compile(dialect=dialect)) for c in table.constraints))
    for table in (cancellations, cancellation_lines):
        assert shape(table) == shape(Base.metadata.tables[table.name])
        assert table.name in security.RUNTIME_READ_TABLES and table.name in security.RUNTIME_INSERT_TABLES


def tooling():
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE alembic_version (version_num TEXT NOT NULL)')
        db.exec_driver_sql("INSERT INTO alembic_version VALUES ('20261219_0170')")
    return engine


def test_sqlite_empty_roundtrip_and_all_business_writes_refused():
    with tooling().begin() as db:
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261220_0171'")
        for table in SQLITE['TABLES']:
            with pytest.raises(IntegrityError, match='cancellation writes require PostgreSQL16'):
                db.exec_driver_sql(f'INSERT INTO {table} DEFAULT VALUES')
        SQLITE['transition'](db, up=False)
        assert all(not SQLITE['objects'](db, table) for table in SQLITE['TABLES'])


def test_sqlite_unknown_predecessor_or_drift_refused_before_destruction():
    with tooling().begin() as db:
        db.exec_driver_sql("UPDATE alembic_version SET version_num='unknown'")
        with pytest.raises(ValueError, match='exact single SQLite predecessor'):
            SQLITE['transition'](db, up=True)
        assert all(not SQLITE['objects'](db, table) for table in SQLITE['TABLES'])
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261219_0170'")
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261220_0171'")
        guard = next(iter(SQLITE['guards'](SQLITE['TABLES'][1])))
        db.exec_driver_sql(f'DROP TRIGGER {guard}')
        with pytest.raises(ValueError, match='exact SQLite schema'):
            SQLITE['transition'](db, up=False)
        assert all(SQLITE['objects'](db, table) for table in SQLITE['TABLES'])
