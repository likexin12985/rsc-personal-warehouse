"""Scratch DB bootstrap stays scoped and cleans up on dependency failure."""
from contextlib import contextmanager

import pytest

import test_postgresql16_release_gate as gate


@pytest.mark.parametrize('bootstrap_fails', [False, True])
def test_scratch_database_provisions_uuid_as_its_own_dba(monkeypatch, bootstrap_fails):
    bootstrap = (gate.CLOUD_ROOT / 'deployment/postgres-init/20-loss-uuid.sql').read_text()
    calls = []
    dropped = []

    class Cursor:
        def __init__(self, parameters):
            self.parameters = parameters

        def execute(self, statement):
            calls.append((self.parameters.copy(), statement))
            if statement == bootstrap and bootstrap_fails:
                raise RuntimeError('private UUID provisioning refused')

    class Connection:
        def __init__(self, parameters):
            self.parameters = parameters

        @contextmanager
        def cursor(self):
            yield Cursor(self.parameters)

    @contextmanager
    def connect(**parameters):
        yield Connection(parameters)

    monkeypatch.setattr(gate, '_admin_parameters', lambda: {
        'dbname': gate.DATABASE_NAME, 'user': 'postgres',
    })
    monkeypatch.setattr(gate.psycopg, 'connect', connect)
    monkeypatch.setattr(gate, '_drop_opening_backfill_database', dropped.append)
    if bootstrap_fails:
        with pytest.raises(RuntimeError, match='private UUID provisioning refused'):
            gate._create_opening_backfill_database()
    else:
        created = gate._create_opening_backfill_database()

    parameters, = [parameters for parameters, statement in calls if statement == bootstrap]
    assert parameters['user'] == 'postgres'
    scratch = parameters['dbname']
    assert scratch.startswith(gate.OPENING_BACKFILL_DATABASE_PREFIX)
    assert scratch != gate.DATABASE_NAME
    assert parameters['autocommit'] is True
    # A valid historical upgrade needs the backup reader's default grants in
    # this database, not merely in the primary CI service. Also preserve the
    # private-function default and never grant API writes to future tables.
    defaults = [(params, statement) for params, statement in calls
                if isinstance(statement, str) and statement.startswith('ALTER DEFAULT PRIVILEGES')]
    assert len(defaults) == 5
    assert all(params == parameters for params, _ in defaults)
    prefix = 'ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator IN SCHEMA public '
    assert {statement.removeprefix(prefix) for _, statement in defaults} == {
        'REVOKE ALL ON TABLES FROM PUBLIC',
        'GRANT SELECT ON TABLES TO star_oam_backup',
        'REVOKE ALL ON SEQUENCES FROM PUBLIC',
        'GRANT SELECT ON SEQUENCES TO star_oam_backup',
        'REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC',
    }
    assert calls.index(defaults[-1]) < next(i for i, (_, statement) in enumerate(calls)
                                         if statement == bootstrap)
    if bootstrap_fails:
        assert dropped == [scratch]
    else:
        assert created == scratch
        assert dropped == []


def test_legacy_seed_uses_exact_old_graph_then_releases_it_before_current_upgrade(monkeypatch):
    from pathlib import Path
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import pg16_legacy_opening_fixture

    scratch = gate.OPENING_BACKFILL_DATABASE_PREFIX + 'synthetic'
    calls = []
    engine = object()
    disposed = []

    class Owner:
        def dispose(self):
            disposed.append(self)

    engine = Owner()

    def migrate(*arguments, database_name, config_path=None):
        assert database_name == scratch
        scripts = ScriptDirectory.from_config(Config(str(config_path)))
        assert scripts.get_heads() == ['20260903_0051']
        assert len(tuple(scripts.walk_revisions())) == 51
        calls.append((arguments, Path(config_path)))

    def seed(owner, *, mutate_policy_after_completion):
        assert owner is engine and mutate_policy_after_completion is True
        # The isolated graph is scoped to the initial upgrade, not later
        # current-head verification or execution of the historical fixture.
        assert not calls[0][1].exists()
        return {'synthetic': 'legacy evidence'}

    monkeypatch.setattr(gate, '_run_alembic', migrate)
    monkeypatch.setattr(gate, '_isolated_current_revision', lambda name:
        gate.STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION if name == scratch else None)
    monkeypatch.setattr(gate, '_legacy_backfill_engine', lambda name: engine if name == scratch else None)
    monkeypatch.setattr(pg16_legacy_opening_fixture, 'seed_legacy_completion', seed)
    assert gate._seed_0051_observation_only_completion(scratch,
        mutate_policy_after_completion=True) == {'synthetic': 'legacy evidence'}
    assert calls[0][0] == ('upgrade', '20260903_0051')
    assert disposed == [engine]
