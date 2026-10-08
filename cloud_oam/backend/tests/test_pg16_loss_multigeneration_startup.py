"""Migration metadata remains unreadable by the API in native gate startup."""
import pytest
import pg16_loss_multigeneration_gate as gate


@pytest.mark.parametrize('revisions', [[gate.HEAD], [gate.PREVIOUS], [], [gate.HEAD, gate.PREVIOUS]])
def test_startup_checks_revision_as_migrator_without_expanding_api_access(monkeypatch, revisions):
    observed, validations = [], []

    class Connection:
        def __init__(self, role):
            self.role = role

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def scalar(self, query):
            sql = str(query)
            observed.append((self.role, sql))
            if sql == 'SELECT current_user':
                return self.role
            if sql == 'SELECT version_num FROM alembic_version':
                if self.role != 'star_oam_migrator':
                    raise PermissionError('API cannot read alembic_version')
                return revisions
            raise AssertionError('unexpected startup query')

        def scalars(self, query):
            from types import SimpleNamespace
            values = self.scalar(query)
            return SimpleNamespace(all=lambda: values)

        def connect(self):
            return self

    owner, api = Connection('star_oam_migrator'), Connection('star_oam_api')

    def validate(engine, **kwargs):
        assert engine is api
        validations.append(kwargs)

    monkeypatch.setattr(gate, 'validate_production_database_security', validate)
    if revisions == [gate.HEAD]:
        gate.assert_current_runtime(owner, api)
    else:
        with pytest.raises(AssertionError):
            gate.assert_current_runtime(owner, api)
    assert validations == [dict(expected_runtime_role='star_oam_api',
        expected_migration_role='star_oam_migrator')]
    assert observed == [('star_oam_api', 'SELECT current_user'),
        ('star_oam_migrator', 'SELECT version_num FROM alembic_version')]
