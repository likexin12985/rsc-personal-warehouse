"""Warehouse acceptance activation preserves history and private DB authority."""
from pathlib import Path
import json
import runpy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects import postgresql

import app.models
from app.database import Base

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'alembic/rejection_receipt_0174'
SQLITE = runpy.run_path(str(FOLDER / 'sqlite_transition.py'))


def test_frozen_receipts_match_runtime_metadata_and_private_permissions():
    from app import database_security as security
    from app.material_request_rejection_receipt_readiness import DATA as ready
    runtime = json.loads((ROOT / 'app/material_request_rejection_receipt_security.json').read_text())
    revision = runpy.run_path(str(ROOT / 'alembic/versions/20261223_0174_rejection_warehouse_receipts.py'))
    assert revision['revision'] == '20261223_0174'
    assert revision['down_revision'] == '20261222_0173'
    frozen = revision['_support']('transition')['DATA']
    assert {k: v for k, v in frozen.items() if k not in ('statements', 'revision', 'previousRevision')} == runtime
    previous = json.loads((ROOT / 'alembic/rejection_progress_0173/readiness.json').read_text())
    assert ready['before'] == previous['after']
    from migration_source_expectations import current_source_body
    assert security._stock_scrap_readiness.DATA['after']['prosrc'] == current_source_body('20261223_0174', 'public.' + ready['after']['signature'], ready['after']['prosrc'])
    assert revision['_sources']() == {'public.' + ready['before']['signature']:
        (ready['before']['prosrc'], ready['after']['prosrc'])}
    dialect = postgresql.dialect()
    for name in SQLITE['TABLES']:
        table = Base.metadata.tables[name]
        assert str(CreateTable(table).compile(dialect=dialect)).strip() in frozen['statements']
        for index in table.indexes:
            assert str(CreateIndex(index).compile(dialect=dialect)).strip() in frozen['statements']
        assert name in security.RUNTIME_READ_TABLES
        assert name in security.RUNTIME_INSERT_TABLES
        assert {a['privilege'] for a in runtime['tables'][name]['after']['acl']
                if a['grantee'] == 'star_oam_api'} == {'SELECT', 'INSERT'}
    assert len(runtime['functions']) == 5
    for signature, change in runtime['functions'].items():
        assert change['before'] is None
        assert not any(a['grantee'] in ('PUBLIC', 'star_oam_api') for a in change['after']['acl'])
        coordinate = change['after']['proname'], signature.split('(', 1)[1][:-1]
        assert coordinate in security.FORMAL_FILE_INTERNAL_FUNCTIONS
        assert coordinate not in security.RUNTIME_EXECUTE_FUNCTIONS


def tooling():
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE alembic_version (version_num TEXT NOT NULL)')
        db.exec_driver_sql("INSERT INTO alembic_version VALUES ('20261222_0173')")
    return engine


def test_sqlite_empty_roundtrip_refuses_business_insert():
    with tooling().begin() as db:
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261223_0174'")
        for table in SQLITE['TABLES']:
            with pytest.raises(IntegrityError, match='writes require PostgreSQL16'):
                db.exec_driver_sql(f'INSERT INTO {table} DEFAULT VALUES')
        SQLITE['transition'](db, up=False)
        assert all(not SQLITE['objects'](db, table) for table in SQLITE['TABLES'])


def test_sqlite_wrong_head_and_schema_drift_leave_all_tables_intact():
    with tooling().begin() as db:
        db.exec_driver_sql("INSERT INTO alembic_version VALUES ('unexpected')")
        with pytest.raises(ValueError, match='exact single SQLite predecessor'):
            SQLITE['transition'](db, up=True)
        assert all(not SQLITE['objects'](db, table) for table in SQLITE['TABLES'])
        db.exec_driver_sql("DELETE FROM alembic_version WHERE version_num='unexpected'")
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261223_0174'")
        last = SQLITE['TABLES'][-1]
        db.exec_driver_sql('DROP TRIGGER ' + next(iter(SQLITE['guards'](last))))
        before = {t: SQLITE['objects'](db, t) for t in SQLITE['TABLES']}
        with pytest.raises(ValueError, match='exact SQLite schema'):
            SQLITE['transition'](db, up=False)
        assert {t: SQLITE['objects'](db, t) for t in SQLITE['TABLES']} == before


def test_sqlite_existing_child_history_blocks_downgrade_and_all_mutations():
    with tooling().begin() as db:
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261223_0174'")
        table = 'material_request_rejection_receipt_serials'
        guard, sql = next(iter(SQLITE['guards'](table).items()))
        # Privileged tooling fixture: retained child history must prevent every
        # destructive schema operation even when earlier new tables are empty.
        db.exec_driver_sql('DROP TRIGGER ' + guard)
        db.exec_driver_sql(f"INSERT INTO {table} (receipt_id,return_id,serial_id,result,damaged) "
                          "VALUES ('a','b','c','shortage',0)")
        db.exec_driver_sql(sql)
        before = {t: SQLITE['objects'](db, t) for t in SQLITE['TABLES']}
        for statement in (f"UPDATE {table} SET result='rejected'", f'DELETE FROM {table}'):
            with pytest.raises(IntegrityError, match='writes require PostgreSQL16'):
                db.exec_driver_sql(statement)
        with pytest.raises(ValueError, match='immutable SQLite history requires retention'):
            SQLITE['transition'](db, up=False)
        assert {t: SQLITE['objects'](db, t) for t in SQLITE['TABLES']} == before
        assert db.exec_driver_sql(f'SELECT count(*) FROM {table}').scalar() == 1
