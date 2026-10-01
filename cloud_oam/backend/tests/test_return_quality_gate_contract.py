"""CI dispatch and fail-closed setup checks; never a substitute for PG16 proof."""
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('tracking', ['quantity', 'serial'])
@pytest.mark.parametrize('scenario', ['whole', 'mixed'])
def test_quality_ci_leg_routes_exact_scenario_and_disposes_engines(monkeypatch, tracking, scenario):
    import test_postgresql16_stock_loss_release_gate as entry
    import pg16_return_quality_gate as quality
    monkeypatch.setenv('RSC_PG16_LOSS_TRACKING', tracking)
    monkeypatch.setenv('RSC_PG16_LOSS_FLOW', 'return_quality_' + scenario)
    monkeypatch.setattr(entry.gate, '_gate_enabled', lambda: True)
    for name in ('_assert_fresh_disposable_postgresql16', '_bootstrap_roles', '_provision_edge_receiver_role'):
        monkeypatch.setattr(entry.gate, name, lambda: None)
    monkeypatch.setattr(entry.gate, '_role_password', lambda role: 'synthetic-fixture-only')
    monkeypatch.setattr(entry.gate, '_sqlalchemy_url', lambda **kwargs: kwargs['role'])
    disposed, observed = [], []
    monkeypatch.setattr(entry, 'create_engine', lambda role, **kwargs: SimpleNamespace(dispose=lambda: disposed.append(role)))
    def run(engines, **kwargs):
        assert set(kwargs) == {'tracking', 'scenario', 'migrate', 'provision'}
        assert callable(kwargs['migrate']) and callable(kwargs['provision'])
        observed.append((tuple(engines), kwargs['tracking'], kwargs['scenario']))
        return {'passed': True}
    monkeypatch.setattr(quality, 'release', run)
    entry.test_postgresql16_stock_loss_release_gate()
    roles = ('star_oam_migrator', 'star_oam_api', entry.gate.EDGE_RECEIVER_ROLE)
    assert observed == [(roles, tracking, scenario)]
    assert tuple(disposed) == roles


@pytest.mark.parametrize('tracking,scenario', [('', 'mixed'), ('both', 'whole'), ('serial', ''), ('quantity', 'normal')])
def test_invalid_quality_leg_stops_before_database(tracking, scenario):
    from pg16_return_quality_gate import release
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid quality gate accessed database setup')
    with pytest.raises(ValueError, match='explicit quantity/serial tracking and whole/mixed quality scenario required'):
        release({}, tracking=tracking, scenario=scenario, migrate=forbidden, provision=forbidden)
