"""Formal 0173 preserves registrations and freezes active-claim enforcement."""
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
from app.material_request_rejection_progress_schema import progress
from app.material_request_rejection_return_schema import return_serials

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'alembic/rejection_progress_0173'
SQLITE = runpy.run_path(str(FOLDER / 'sqlite_transition.py'))


def test_frozen_progress_metadata_and_exact_forward_function_sources():
    from app import database_security as security
    from app.material_request_rejection_progress_readiness import DATA as ready
    runtime = json.loads((ROOT / 'app/material_request_rejection_progress_security.json').read_text())
    revision = runpy.run_path(str(ROOT / 'alembic/versions/20261222_0173_rejection_return_progress.py'))
    assert revision['revision'] == '20261222_0173' and revision['down_revision'] == '20261221_0172'
    frozen = revision['_support']('transition')['DATA']
    assert {k:v for k,v in frozen.items() if k not in ('statements','revision','previousRevision')} == runtime
    assert len(revision['_sources']()) == 3
    from migration_source_expectations import current_source_body
    assert security._stock_scrap_readiness.DATA['after']['prosrc'] == current_source_body('20261222_0173', 'public.'+ready['after']['signature'], ready['after']['prosrc'])
    dialect = postgresql.dialect()
    def shape(table):
        return ([str(CreateColumn(c).compile(dialect=dialect)) for c in table.columns],
                sorted(str(AddConstraint(c).compile(dialect=dialect)) for c in table.constraints))
    for table in (progress, return_serials):
        assert shape(table) == shape(Base.metadata.tables[table.name])
        assert table.name in security.RUNTIME_READ_TABLES and table.name in security.RUNTIME_INSERT_TABLES
    original = json.loads((ROOT / 'alembic/rejection_return_0172/catalog.json').read_text())
    for signature in ('rsc_guard_rejection_return_insert_0172()', 'rsc_guard_rejection_return_serial_0172()'):
        assert frozen['functions'][signature]['before'] == original['functions'][signature]['after']
        assert frozen['functions'][signature]['after'] != frozen['functions'][signature]['before']
    serial_catalog = frozen['tables'][return_serials.name]
    assert any(c['name'] == 'uq_rejection_return_serial_origin' for c in serial_catalog['before']['constraints'])
    assert all(c['name'] != 'uq_rejection_return_serial_origin' for c in serial_catalog['after']['constraints'])


def tooling():
    engine = create_engine('sqlite://')
    predecessor = runpy.run_path(str(ROOT / 'alembic/rejection_return_0172/sqlite_transition.py'))
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE alembic_version (version_num TEXT NOT NULL)')
        db.exec_driver_sql("INSERT INTO alembic_version VALUES ('20261220_0171')")
        predecessor['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261221_0172'")
    return engine


def test_sqlite_roundtrip_preserves_original_guards_and_refuses_business_writes():
    with tooling().begin() as db:
        original = SQLITE['objects'](db, return_serials.name)
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261222_0173'")
        for table in (progress.name, return_serials.name):
            with pytest.raises(IntegrityError, match='writes require PostgreSQL16'):
                db.exec_driver_sql(f'INSERT INTO {table} DEFAULT VALUES')
        SQLITE['transition'](db, up=False)
        assert SQLITE['objects'](db, return_serials.name) == original
        assert SQLITE['objects'](db, progress.name) == {}


def test_sqlite_drift_refused_before_child_rebuild():
    with tooling().begin() as db:
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261222_0173'")
        serials_before = SQLITE['objects'](db, return_serials.name)
        db.exec_driver_sql('DROP TRIGGER rsc_rejection_progress_insert_0173')
        with pytest.raises(ValueError, match='exact SQLite schema'):
            SQLITE['transition'](db, up=False)
        assert SQLITE['objects'](db, return_serials.name) == serials_before
        assert SQLITE['objects'](db, progress.name)


def test_sqlite_unexpected_serial_history_never_discarded_on_upgrade():
    with tooling().begin() as db:
        name = 'rsc_rejection_return_1_insert_0172'
        sql = SQLITE['DATA']['before'][return_serials.name][name]
        db.exec_driver_sql('DROP TRIGGER ' + name)
        db.exec_driver_sql("INSERT INTO material_request_rejection_return_serials VALUES ('a','b','c')")
        db.exec_driver_sql(sql)
        with pytest.raises(ValueError, match='immutable SQLite history'):
            SQLITE['transition'](db, up=True)
        assert db.exec_driver_sql('SELECT count(*) FROM material_request_rejection_return_serials').scalar() == 1
        assert SQLITE['objects'](db, progress.name) == {}
