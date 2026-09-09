from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/20260920_0080_inbound_posting_acl.py"


def test_inbound_posting_acl_is_forward_and_reversible(monkeypatch):
    migration = runpy.run_path(str(MIGRATION))
    statements = []
    monkeypatch.setattr(migration["op"], "execute", statements.append)
    monkeypatch.setattr(
        migration["op"],
        "get_bind",
        lambda: type("Bind", (), {"dialect": type("Dialect", (), {"name": "postgresql"})()})(),
    )
    migration["upgrade"]()
    migration["downgrade"]()
    assert migration["down_revision"] == "20260919_0079"
    assert statements == [
        "GRANT SELECT, INSERT ON TABLE public.inbound_postings TO star_oam_api",
        "REVOKE SELECT, INSERT ON TABLE public.inbound_postings FROM star_oam_api",
    ]
