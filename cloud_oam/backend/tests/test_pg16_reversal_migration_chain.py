"""Historical probes must isolate DDL and clean up their own failed fixtures."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

import pg16_work_order_reversal_boundary_gate as probe


def test_historical_probe_rejects_non_postgresql_before_fixture_writes():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    try:
        with pytest.raises(RuntimeError, match='isolated PostgreSQL 0096'):
            probe.assert_legacy_reversal_migration_candidates(engine)
        with engine.connect() as db:
            assert db.exec_driver_sql('SELECT count(*) FROM sqlite_master').scalar() == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize('failure', [None, 'migration', 'probe'])
def test_isolated_probe_never_migrates_current_database_and_cleans_up(monkeypatch, failure):
    import test_postgresql16_release_gate as gate

    calls = []
    database = 'synthetic_disposable_history'
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, statement): return SimpleNamespace(all=lambda: [('unchanged',)])
    current = SimpleNamespace(connect=Connection)
    isolated = SimpleNamespace(dispose=lambda: calls.append(('dispose', database)))
    monkeypatch.setattr(probe, 'snapshot', lambda _: ('current inventory preserved',))
    monkeypatch.setattr(gate, '_create_opening_backfill_database', lambda: database)
    monkeypatch.setattr(gate, '_drop_opening_backfill_database', lambda value: calls.append(('drop', value)))
    def migrate(action, revision, *, database_name):
        calls.append((action, revision, database_name))
        if failure == 'migration': raise RuntimeError('synthetic migration failed')
    monkeypatch.setattr(gate, '_run_alembic', migrate)
    monkeypatch.setattr(gate, '_legacy_backfill_engine', lambda value: isolated if value == database else pytest.fail('wrong database'))
    def verify(engine):
        assert engine is isolated and engine is not current
        calls.append(('probe', database))
        if failure == 'probe': raise RuntimeError('synthetic probe failed')
    monkeypatch.setattr(probe, 'assert_legacy_reversal_migration_candidates', verify)
    if failure:
        with pytest.raises(RuntimeError, match='synthetic '+failure+' failed'):
            probe.assert_reversal_migration_rejects_detached_history(current, current)
    else:
        probe.assert_reversal_migration_rejects_detached_history(current, current)
    assert calls[-1] == ('drop', database)
    ddl = [row for row in calls if row[0] in ('upgrade','downgrade')]
    expected = [('upgrade','20261006_0096',database), ('upgrade','20261007_0097',database), ('downgrade','20261006_0096',database)]
    assert ddl == (expected[:1] if failure == 'migration' else expected)
    if failure != 'migration':
        assert calls[-2] == ('dispose', database)
