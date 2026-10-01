"""Multi-generation CI legs retain disposable admission and engine cleanup."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("flow,scenario", [
    ("correction_generations", "generations"),
    ("correction_seal_retention", "seal_retention"),
])
@pytest.mark.parametrize("tracking", ["quantity", "serial"])
@pytest.mark.parametrize("fails", [False, True])
def test_multigeneration_matrix_routes_scenario_and_cleans_up(monkeypatch, flow, scenario, tracking, fails):
    import pg16_loss_multigeneration_gate as correction
    import test_postgresql16_stock_loss_release_gate as entry

    monkeypatch.setenv("RSC_PG16_LOSS_FLOW", flow)
    monkeypatch.setenv("RSC_PG16_LOSS_TRACKING", tracking)
    monkeypatch.setattr(entry.gate, "_gate_enabled", lambda: True)
    for name in ("_assert_fresh_disposable_postgresql16", "_bootstrap_roles", "_provision_edge_receiver_role"):
        monkeypatch.setattr(entry.gate, name, lambda: None)
    monkeypatch.setattr(entry.gate, "_role_password", lambda role: "synthetic-only")
    monkeypatch.setattr(entry.gate, "_sqlalchemy_url", lambda **kw: kw["role"])
    disposed, calls = [], []
    monkeypatch.setattr(entry, "create_engine", lambda role, **kw:
        SimpleNamespace(dispose=lambda: disposed.append(role)))

    def run(engines, **kwargs):
        assert set(kwargs) == {"tracking", "scenario", "migrate", "provision"}
        assert callable(kwargs["migrate"]) and callable(kwargs["provision"])
        calls.append((tuple(engines), kwargs["tracking"], kwargs["scenario"]))
        if fails:
            raise RuntimeError("synthetic correction failure")
        return {"passed": True}

    monkeypatch.setattr(correction, "release", run)
    if fails:
        with pytest.raises(RuntimeError, match="synthetic correction failure"):
            entry.test_postgresql16_stock_loss_release_gate()
    else:
        entry.test_postgresql16_stock_loss_release_gate()
    roles = ("star_oam_migrator", "star_oam_api", entry.gate.EDGE_RECEIVER_ROLE)
    assert calls == [(roles, tracking, scenario)]
    assert tuple(disposed) == roles


@pytest.mark.parametrize("tracking,scenario", [
    ("", "generations"), ("both", "generations"),
    ("quantity", ""), ("serial", "production"), ("quantity", "restore_available"),
])
def test_invalid_multigeneration_scenario_refuses_before_database(tracking, scenario):
    from pg16_loss_multigeneration_gate import release

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid scenario reached database setup")

    with pytest.raises(ValueError, match="explicit multigeneration tracking and scenario required"):
        release({}, tracking=tracking, scenario=scenario, migrate=forbidden, provision=forbidden)
