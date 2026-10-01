"""CI dispatch, disposable-boundary and cleanup checks; native proof is separate."""
from types import SimpleNamespace
import pytest

SCENARIOS = ('seals', 'http', 'negative', 'seal_first', 'execute_first', 'stop_first', 'outbound_first')


@pytest.mark.parametrize('tracking', ('quantity', 'serial'))
@pytest.mark.parametrize('scenario', SCENARIOS)
def test_stop_ci_leg_routes_scenario_and_disposes_on_failure(monkeypatch, tracking, scenario):
    import test_postgresql16_stock_loss_release_gate as entry
    import pg16_return_stop_gate as stop
    monkeypatch.setenv('RSC_PG16_LOSS_TRACKING', tracking)
    monkeypatch.setenv('RSC_PG16_LOSS_FLOW', 'return_stop_' + scenario)
    monkeypatch.setattr(entry.gate, '_gate_enabled', lambda: True)
    admission = []
    for name in ('_assert_fresh_disposable_postgresql16', '_bootstrap_roles', '_provision_edge_receiver_role'):
        monkeypatch.setattr(entry.gate, name, lambda name=name: admission.append(name))
    monkeypatch.setattr(entry.gate, '_role_password', lambda role: 'synthetic-fixture-only')
    monkeypatch.setattr(entry.gate, '_sqlalchemy_url', lambda **kwargs: kwargs['role'])
    disposed = []
    monkeypatch.setattr(entry, 'create_engine', lambda role, **kwargs: SimpleNamespace(dispose=lambda: disposed.append(role)))

    class BusinessFailure(Exception):
        pass

    def run(engines, **kwargs):
        assert admission == ['_assert_fresh_disposable_postgresql16', '_bootstrap_roles', '_provision_edge_receiver_role']
        assert tuple(engines) == ('star_oam_migrator', 'star_oam_api', entry.gate.EDGE_RECEIVER_ROLE)
        assert set(kwargs) == {'tracking', 'scenario', 'migrate', 'provision'}
        assert kwargs['tracking'] == tracking and kwargs['scenario'] == scenario
        assert callable(kwargs['migrate']) and callable(kwargs['provision'])
        raise BusinessFailure()

    monkeypatch.setattr(stop, 'release', run)
    with pytest.raises(BusinessFailure):
        entry.test_postgresql16_stock_loss_release_gate()
    assert tuple(disposed) == ('star_oam_migrator', 'star_oam_api', entry.gate.EDGE_RECEIVER_ROLE)


@pytest.mark.parametrize('tracking,scenario', (('', 'http'), ('both', 'seals'), ('serial', ''), ('quantity', 'all')))
def test_invalid_stop_leg_cannot_reach_migration(tracking, scenario):
    from pg16_return_stop_gate import release
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid stop leg reached database setup')
    with pytest.raises(ValueError, match='explicit return stop tracking and scenario required'):
        release({}, tracking=tracking, scenario=scenario, migrate=forbidden, provision=forbidden)
