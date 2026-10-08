"""Formal returned compensation: frozen schema, narrow grants and retention."""
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
FOLDER = ROOT / 'alembic/return_compensation_0176'
SQLITE = runpy.run_path(str(FOLDER / 'sqlite_transition.py'))
TABLE = 'material_request_return_compensations'


def test_exact_frozen_schema_private_grants_and_versioned_predecessor_sources():
    from app import database_security as security
    from app.material_request_return_compensation_readiness import DATA as ready
    revision = runpy.run_path(str(ROOT / 'alembic/versions/20261225_0176_return_compensations.py'))
    assert (revision['revision'], revision['down_revision']) == ('20261225_0176', '20261224_0175')
    frozen = revision['_support']('transition')['DATA']
    runtime = json.loads((ROOT / 'app/material_request_return_compensation_security.json').read_text())
    assert {k: v for k, v in frozen.items() if k not in ('statements', 'revision', 'previousRevision')} == runtime
    assert ready['before'] == json.loads((ROOT / 'alembic/rejection_inbound_0175/readiness.json').read_text())['after']
    sources = {'public.' + key: (change['before']['prosrc'], change['after']['prosrc'])
               for key, change in frozen['functions'].items() if change['before'] is not None}
    sources['public.' + ready['before']['signature']] = (ready['before']['prosrc'], ready['after']['prosrc'])
    assert len(sources) == 4 and revision['_sources']() == sources
    assert len(frozen['statements']) == 22
    table = Base.metadata.tables[TABLE]
    dialect = postgresql.dialect()
    assert str(CreateTable(table).compile(dialect=dialect)).strip() in [statement.strip() for statement in frozen['statements']]
    for index in table.indexes:
        assert str(CreateIndex(index).compile(dialect=dialect)).strip() in [statement.strip() for statement in frozen['statements']]
    assert TABLE in security.RUNTIME_READ_TABLES and TABLE in security.RUNTIME_INSERT_TABLES
    assert {a['privilege'] for a in runtime['tables'][TABLE]['after']['acl'] if a['grantee'] == 'star_oam_api'} == {'SELECT', 'INSERT'}
    assert sum(c['before'] is None for c in runtime['functions'].values()) == 4
    for signature, change in runtime['functions'].items():
        if change['before'] is not None:
            continue
        assert not any(a['grantee'] in ('PUBLIC', 'star_oam_api') for a in change['after']['acl'])
        coordinate = change['after']['proname'], signature.split('(', 1)[1][:-1]
        assert coordinate in security.FORMAL_FILE_INTERNAL_FUNCTIONS
        assert coordinate not in security.RUNTIME_EXECUTE_FUNCTIONS


def tooling():
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE alembic_version (version_num TEXT NOT NULL)')
        db.exec_driver_sql("INSERT INTO alembic_version VALUES ('20261224_0175')")
    return engine


def test_empty_schema_roundtrip_rejects_sqlite_business_writes():
    with tooling().begin() as db:
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261225_0176'")
        with pytest.raises(IntegrityError, match='writes require PostgreSQL16'):
            db.exec_driver_sql(f'INSERT INTO {TABLE} DEFAULT VALUES')
        SQLITE['transition'](db, up=False)
        assert not SQLITE['objects'](db, TABLE)


def test_predecessor_drift_and_retained_history_cannot_remove_schema():
    with tooling().begin() as db:
        db.exec_driver_sql("INSERT INTO alembic_version VALUES ('unexpected')")
        with pytest.raises(ValueError, match='exact single SQLite predecessor'):
            SQLITE['transition'](db, up=True)
        assert not SQLITE['objects'](db, TABLE)
        db.exec_driver_sql("DELETE FROM alembic_version WHERE version_num='unexpected'")
        SQLITE['transition'](db, up=True)
        db.exec_driver_sql("UPDATE alembic_version SET version_num='20261225_0176'")
        guard, sql = next(iter(SQLITE['guards'](TABLE).items()))
        db.exec_driver_sql('DROP TRIGGER ' + guard)
        columns = db.exec_driver_sql(f'PRAGMA table_info({TABLE})').all()
        values = tuple(1 if 'INT' in c[2] or 'NUMERIC' in c[2] else '{}' if c[1]=='evidence_jsonb' else 'retained' for c in columns)
        db.exec_driver_sql(f'INSERT INTO {TABLE} (' + ','.join(c[1] for c in columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')', values)
        db.exec_driver_sql(sql)
        before = SQLITE['objects'](db, TABLE)
        for command in (f"UPDATE {TABLE} SET reason='changed'", f'DELETE FROM {TABLE}'):
            with pytest.raises(IntegrityError, match='writes require PostgreSQL16'):
                db.exec_driver_sql(command)
        with pytest.raises(ValueError, match='immutable SQLite history requires retention'):
            SQLITE['transition'](db, up=False)
        assert SQLITE['objects'](db, TABLE) == before
        assert db.exec_driver_sql(f'SELECT count(*) FROM {TABLE}').scalar() == 1
