"""Warehouse posting activation preserves history and private DB authority."""
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
FOLDER = ROOT / 'alembic/rejection_inbound_0175'
SQLITE = runpy.run_path(str(FOLDER / 'sqlite_transition.py'))


def test_frozen_inbounds_match_runtime_metadata_and_private_permissions():
    from app import database_security as security
    from app.material_request_rejection_inbound_readiness import DATA as ready
    runtime = json.loads((ROOT / 'app/material_request_rejection_inbound_security.json').read_text())
    revision = runpy.run_path(str(ROOT / 'alembic/versions/20261224_0175_rejection_warehouse_inbounds.py'))
    assert revision['revision'] == '20261224_0175'
    assert revision['down_revision'] == '20261223_0174'
    frozen = revision['_support']('transition')['DATA']
    assert {k: v for k, v in frozen.items() if k not in ('statements', 'revision', 'previousRevision')} == runtime
    previous = json.loads((ROOT / 'alembic/rejection_receipt_0174/readiness.json').read_text())
    assert ready['before'] == previous['after']
    from migration_source_expectations import current_source_body
    assert security._stock_scrap_readiness.DATA['after']['prosrc'] == current_source_body('20261224_0175', 'public.' + ready['after']['signature'], ready['after']['prosrc'])
    sources = {'public.' + signature: (change['before']['prosrc'], change['after']['prosrc'])
        for signature, change in frozen['functions'].items() if change['before'] is not None}
    sources['public.' + ready['before']['signature']] = (ready['before']['prosrc'], ready['after']['prosrc'])
    assert len(sources) == 2 and revision['_sources']() == sources
    assert len(frozen['statements']) == 48
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
    assert len(runtime['functions']) == 7
    assert sum(c['before'] is None for c in runtime['functions'].values()) == 6
    for signature, change in runtime['functions'].items():
        if change['before'] is not None:
            assert signature == 'rsc_require_opening_observation_account_0023()'
            continue
        assert not any(a['grantee'] in ('PUBLIC', 'star_oam_api') for a in change['after']['acl'])
        coordinate = change['after']['proname'], signature.split('(', 1)[1][:-1]
        assert coordinate in security.FORMAL_FILE_INTERNAL_FUNCTIONS
        assert coordinate not in security.RUNTIME_EXECUTE_FUNCTIONS


def tooling():
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE alembic_version (version_num TEXT NOT NULL)')
        db.exec_driver_sql("INSERT INTO alembic_version VALUES ('20261223_0174')")
    return engine


def test_sqlite_empty_roundtrip_refuses_business_insert():
    with tooling().begin() as db:
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261224_0175'")
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
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261224_0175'")
        last = SQLITE['TABLES'][-1]
        db.exec_driver_sql('DROP TRIGGER ' + next(iter(SQLITE['guards'](last))))
        before = {t: SQLITE['objects'](db, t) for t in SQLITE['TABLES']}
        with pytest.raises(ValueError, match='exact SQLite schema'):
            SQLITE['transition'](db, up=False)
        assert {t: SQLITE['objects'](db, t) for t in SQLITE['TABLES']} == before


def test_sqlite_existing_child_history_blocks_downgrade_and_all_mutations():
    with tooling().begin() as db:
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261224_0175'")
        table = 'material_request_rejection_inbound_serials'
        guard, sql = next(iter(SQLITE['guards'](table).items()))
        # Privileged tooling fixture: retained child history must prevent every
        # destructive schema operation even when earlier new tables are empty.
        db.exec_driver_sql('DROP TRIGGER ' + guard)
        db.exec_driver_sql(f"INSERT INTO {table} (inbound_id,serial_id,condition_code) "
                          "VALUES ('a','b','new')")
        db.exec_driver_sql(sql)
        before = {t: SQLITE['objects'](db, t) for t in SQLITE['TABLES']}
        for statement in (f"UPDATE {table} SET condition_code='damaged'", f'DELETE FROM {table}'):
            with pytest.raises(IntegrityError, match='writes require PostgreSQL16'):
                db.exec_driver_sql(statement)
        with pytest.raises(ValueError, match='immutable SQLite history requires retention'):
            SQLITE['transition'](db, up=False)
        assert {t: SQLITE['objects'](db, t) for t in SQLITE['TABLES']} == before
        assert db.exec_driver_sql(f'SELECT count(*) FROM {table}').scalar() == 1
