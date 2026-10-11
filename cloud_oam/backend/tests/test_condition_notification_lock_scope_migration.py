"""Focused source and migration checks for the 0182 PG16 lock fix."""

from hashlib import sha256
from io import StringIO
from pathlib import Path
import runpy

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine


PATH = Path(__file__).parents[1] / 'alembic/versions/20261231_0182_condition_notification_lock_scope.py'


def test_successor_keeps_frozen_catalog_and_only_scopes_notification_lock():
    m = runpy.run_path(str(PATH))
    legacy, fixed = m['_sources']()[m['SIGNATURE']]
    assert m['revision'] == '20261231_0182'
    assert m['down_revision'] == '20261230_0181'
    assert sha256(legacy.encode()).hexdigest() == m['LEGACY_HASH']
    assert sha256(fixed.encode()).hexdigest() == m['FIXED_HASH']
    assert fixed.replace(m['LOCK_SCOPE_GUARD'], '') == legacy
    assert fixed.index("TG_TABLE_NAME='notification_events'") < fixed.index('FOR UPDATE')
    assert fixed.index("current_setting('transaction_isolation')") < fixed.index("TG_TABLE_NAME='notification_events'")
    assert 'rsc_condition_decision_seal_relevant' in fixed
    assert "business_type') IN ('stock_condition_decision_seal','stock_condition_event')" in fixed
    assert "event_type',prior->>'event_type') LIKE 'stock_condition.%'" in fixed
    assert "schema_version',prior->'payload_jsonb'->>'schema_version')='condition_result/1'" in fixed
    assert 'LIKE \'condition-decision-seal:%\'' in fixed
    # JSON fields are nullable for ordinary notification projections.  Keep
    # the guard two-valued so NULL metadata cannot fall through to FOR UPDATE.
    # A request_id/request_reference alone is deliberately not a lock marker:
    # shipment and receipt notifications carry request_id in their payload.
    assert "COALESCE(COALESCE(next_row->>'request_id',prior->'request_id') LIKE 'condition-decision-seal:%',false)" in fixed
    assert "payload_jsonb'->>'request_reference" not in fixed
    assert "payload_jsonb'->>'request_id',prior->'payload_jsonb'->>'request_id') IS NOT NULL" not in fixed
    assert fixed.count('COALESCE(COALESCE(') == 8
    parser = pytest.importorskip('pglast.parser')
    parser.parse_plpgsql_json(
        'CREATE FUNCTION checked() RETURNS trigger LANGUAGE plpgsql AS $body$'
        + fixed + '$body$')
    ready_old, ready_new = m['_sources']()[m['READY_SIGNATURE']]
    assert sha256(ready_old.encode()).hexdigest() == m['READY_LEGACY_HASH']
    assert sha256(ready_new.encode()).hexdigest() == m['READY_HASH']
    assert ready_new.count('20261230_0181') == 0
    assert ready_new.count('20261231_0182') == 1


def test_runtime_condition_catalog_overlay_admits_successor_source():
    m = runpy.run_path(str(PATH))
    from app import database_security

    row = database_security._condition_catalog.DATA['functions'][
        'rsc_condition_decision_seal_lock()']['after']
    assert row['prosrc'] == m['FIXED_SOURCE']
    assert sha256(row['prosrc'].encode()).hexdigest() == m['FIXED_HASH']
    assert row['definition'].count(m['FIXED_SOURCE']) == 1
    assert m['LEGACY_SOURCE'] not in row['definition']
    readiness_rows = [
        row for family in ('newFunctions', 'replacedFunctions')
        for row in database_security._loss_return_stop_catalog.DATA.get(family, [])
        if row.get('signature') == m['READY_SIGNATURE'].removeprefix('public.')
    ]
    assert len(readiness_rows) == 1
    assert readiness_rows[0]['prosrc'] == m['READY_SOURCE']
    assert readiness_rows[0]['prosrcSha256'] == m['READY_HASH']


@pytest.mark.parametrize('direction', ['upgrade', 'downgrade'])
def test_transition_is_online_source_replacement_with_acl_and_trigger_preflight(direction):
    m = runpy.run_path(str(PATH))
    output = StringIO()
    with Operations.context(MigrationContext.configure(
            dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})):
        m[direction]()
    sql = output.getvalue()
    pytest.importorskip('pglast.parser').parse_sql(sql)
    assert m['LEGACY_HASH'] in sql and m['FIXED_HASH'] in sql
    assert 'ACCESS EXCLUSIVE MODE' in sql
    assert "a.grantee <> p.proowner" in sql
    assert "t.tgenabled='A'" in sql
    for forbidden in ('GRANT ', 'DELETE FROM ', 'UPDATE public.', 'CREATE TABLE ',
                      'DROP TABLE ', 'DROP TRIGGER ', 'CASCADE'):
        assert forbidden not in sql


def test_sqlite_transition_is_a_noop():
    m = runpy.run_path(str(PATH))
    engine = create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
        db.exec_driver_sql('CREATE TABLE retained(id INTEGER PRIMARY KEY, value TEXT)')
        db.exec_driver_sql("INSERT INTO retained VALUES(1,'immutable history')")
        before = tuple(db.exec_driver_sql('SELECT * FROM sqlite_master'))
        m['upgrade']()
        m['downgrade']()
        assert tuple(db.exec_driver_sql('SELECT * FROM sqlite_master')) == before
        assert db.exec_driver_sql('SELECT * FROM retained').one() == (1, 'immutable history')
