"""Formal 0172 frozen schema and schema-tooling refusal boundaries."""
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
from app.material_request_rejection_return_schema import returns, return_serials

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'alembic/rejection_return_0172'
SQLITE = runpy.run_path(str(FOLDER / 'sqlite_transition.py'))


def test_frozen_schema_matches_registered_metadata_and_forward_sources():
    from app import database_security as security
    from app.material_request_rejection_return_readiness import DATA as ready
    runtime = json.loads((ROOT / 'app/material_request_rejection_return_security.json').read_text())
    revision = runpy.run_path(str(ROOT / 'alembic/versions/20261221_0172_rejection_return_registration.py'))
    assert revision['revision'] == '20261221_0172' and revision['down_revision'] == '20261220_0171'
    frozen = revision['_support']('transition')['DATA']
    assert {k:v for k,v in frozen.items() if k not in ('statements','revision','previousRevision')} == runtime
    assert len(revision['_sources']()) == 1
    from migration_source_expectations import current_source_body
    assert security._stock_scrap_readiness.DATA['after']['prosrc'] == current_source_body('20261221_0172', 'public.'+ready['after']['signature'], ready['after']['prosrc'])
    dialect = postgresql.dialect()
    def shape(table):
        return ([str(CreateColumn(c).compile(dialect=dialect)) for c in table.columns],
                sorted(str(AddConstraint(c).compile(dialect=dialect)) for c in table.constraints))
    for table in (returns, return_serials):
        assert shape(table) == shape(Base.metadata.tables[table.name])
        assert table.name in security.RUNTIME_READ_TABLES and table.name in security.RUNTIME_INSERT_TABLES


def tooling():
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE alembic_version (version_num TEXT NOT NULL)')
        db.exec_driver_sql("INSERT INTO alembic_version VALUES ('20261220_0171')")
    return engine


def test_sqlite_empty_roundtrip_and_all_business_writes_refused():
    with tooling().begin() as db:
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261221_0172'")
        for table in SQLITE['TABLES']:
            with pytest.raises(IntegrityError, match='rejection return writes require PostgreSQL16'):
                db.exec_driver_sql(f'INSERT INTO {table} DEFAULT VALUES')
        SQLITE['transition'](db, up=False)
        assert all(not SQLITE['objects'](db, table) for table in SQLITE['TABLES'])


def test_sqlite_unknown_predecessor_or_drift_refused_before_destruction():
    with tooling().begin() as db:
        db.exec_driver_sql("UPDATE alembic_version SET version_num='unknown'")
        with pytest.raises(ValueError, match='exact single SQLite predecessor'):
            SQLITE['transition'](db, up=True)
        assert all(not SQLITE['objects'](db, table) for table in SQLITE['TABLES'])
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261220_0171'")
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261221_0172'")
        guard = next(iter(SQLITE['guards'](SQLITE['TABLES'][1])))
        db.exec_driver_sql(f'DROP TRIGGER {guard}')
        with pytest.raises(ValueError, match='exact SQLite schema'):
            SQLITE['transition'](db, up=False)
        assert all(SQLITE['objects'](db, table) for table in SQLITE['TABLES'])
