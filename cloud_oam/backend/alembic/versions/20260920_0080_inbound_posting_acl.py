"""Grant the runtime API its append-only access to inbound postings."""

from alembic import op


revision = "20260920_0080"
down_revision = "20260919_0079"
branch_labels = depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        return
    if dialect != "postgresql":
        raise RuntimeError("0080 supports only PostgreSQL and SQLite")
    op.execute(
        "GRANT SELECT, INSERT ON TABLE public.inbound_postings TO star_oam_api"
    )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        return
    if dialect != "postgresql":
        raise RuntimeError("0080 supports only PostgreSQL and SQLite")
    op.execute(
        "REVOKE SELECT, INSERT ON TABLE public.inbound_postings FROM star_oam_api"
    )
