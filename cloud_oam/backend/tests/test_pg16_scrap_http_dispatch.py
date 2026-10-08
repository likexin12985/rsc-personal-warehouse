"""Scrap HTTP CI legs retain disposable DB admission and error cleanup."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("tracking", ["quantity", "serial"])
@pytest.mark.parametrize("fails", [False, True])
def test_scrap_http_routes_tracking_and_cleans_up(monkeypatch, tracking, fails):
    import pg16_scrap_http_business as execution
    import test_postgresql16_stock_loss_release_gate as entry

    monkeypatch.setenv("RSC_PG16_LOSS_FLOW", "scrap_http")
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
        assert set(kwargs) == {"tracking", "migrate", "provision"}
        assert callable(kwargs["migrate"]) and callable(kwargs["provision"])
        calls.append((tuple(engines), kwargs["tracking"]))
        if fails:
            raise RuntimeError("synthetic execution failure")
        return {"passed": True}

    monkeypatch.setattr(execution, "release", run)
    if fails:
        with pytest.raises(RuntimeError, match="synthetic execution failure"):
            entry.test_postgresql16_stock_loss_release_gate()
    else:
        entry.test_postgresql16_stock_loss_release_gate()
    roles = ("star_oam_migrator", "star_oam_api", entry.gate.EDGE_RECEIVER_ROLE)
    assert calls == [(roles, tracking)]
    assert tuple(disposed) == roles


def test_invalid_tracking_never_touches_database():
    from pg16_scrap_http_business import release
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid tracking reached migration')
    for tracking in ('', 'both', 'quantity,serial', 'production'):
        with pytest.raises(ValueError, match='tracking must be quantity or serial'):
            release({}, tracking=tracking, migrate=forbidden, provision=forbidden)
