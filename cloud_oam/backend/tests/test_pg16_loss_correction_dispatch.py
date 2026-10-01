"""All correction CI legs retain disposable DB admission and error cleanup."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("flow,kind", [
    ("correction_restore", "restore_available"),
    ("correction_used", "convert_used"),
    ("correction_damaged", "convert_damaged"),
])
@pytest.mark.parametrize("tracking", ["quantity", "serial"])
@pytest.mark.parametrize("fails", [False, True])
def test_correction_matrix_routes_exact_kind_and_cleans_up(monkeypatch, flow, kind, tracking, fails):
    import pg16_loss_correction_gate as correction
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
        assert set(kwargs) == {"tracking", "correction_kind", "migrate", "provision"}
        assert callable(kwargs["migrate"]) and callable(kwargs["provision"])
        calls.append((tuple(engines), kwargs["tracking"], kwargs["correction_kind"]))
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
    assert calls == [(roles, tracking, kind)]
    assert tuple(disposed) == roles


@pytest.mark.parametrize("tracking,kind", [
    ("", "restore_available"), ("both", "restore_available"),
    ("quantity", "scrap"), ("serial", "return_to_region"), ("quantity", ""),
])
def test_unsupported_correction_leg_refuses_before_migration(tracking, kind):
    from pg16_loss_correction_gate import release

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid leg reached database setup")

    with pytest.raises(ValueError, match="explicit correction tracking and disposition required"):
        release({}, tracking=tracking, correction_kind=kind, migrate=forbidden, provision=forbidden)
