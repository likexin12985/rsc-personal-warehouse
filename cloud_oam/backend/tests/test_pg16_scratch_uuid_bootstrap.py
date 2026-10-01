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
    if bootstrap_fails:
        assert dropped == [scratch]
    else:
        assert created == scratch
        assert dropped == []
