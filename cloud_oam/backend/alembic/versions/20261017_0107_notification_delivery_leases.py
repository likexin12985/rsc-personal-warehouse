"""Add explicit notification delivery leases.

The worker lease identifies the only process allowed to record a provider
result.  A stale ``sending`` row is intentionally not requeued automatically:
the provider may already have accepted the message, so replay would duplicate
an external notification.
"""

from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa


revision = "20261017_0107"
down_revision = "20261016_0106"
branch_labels = depends_on = None

RUNTIME_READY_SIGNATURE = "public.rsc_oam_runtime_binding_ready_0044()"
RUNTIME_READY_BODY_SHA256_0106 = (
    "f1f86651bc55ab85977b91d9bb5d02c56fc5b45a1dfd83e13ac53be75d163ca4"
)
RUNTIME_READY_BODY_SHA256_0107 = (
    "fabc93a9f0066f158ba54952bd4417436189f08f92b03686d5ac646f95f1dcb6"
)
# Keep the migration hash names used by the historical catalog gate.
OLD_HASH = RUNTIME_READY_BODY_SHA256_0106
NEW_HASH = RUNTIME_READY_BODY_SHA256_0107


def _replace_readiness(*, upgrade: bool) -> None:
    source = runpy.run_path(
        str(Path(__file__).with_name("20260912_0072_outbound_postings.py"))
    )
    replace = source["_previous"]()["_previous"]()["_previous"]()[
        "_replace_function_source"
    ]
    old_revision, new_revision = (
        (down_revision, revision) if upgrade else (revision, down_revision)
    )
    expected_hash, replacement_hash = (
        (RUNTIME_READY_BODY_SHA256_0106, RUNTIME_READY_BODY_SHA256_0107)
        if upgrade
        else (RUNTIME_READY_BODY_SHA256_0107, RUNTIME_READY_BODY_SHA256_0106)
    )
    replace(
        signature=RUNTIME_READY_SIGNATURE,
        expected_hash=expected_hash,
        replacement_hash=replacement_hash,
        replacements=((old_revision, new_revision),),
        label="notification_delivery_readiness_0107",
    )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0107 supports PostgreSQL and SQLite only")
    if dialect == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
    op.add_column(
        "notification_deliveries",
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_deliveries",
        sa.Column("locked_by", sa.String(length=160), nullable=True),
    )
    op.create_index(
        "ix_notification_deliveries_dispatch",
        "notification_deliveries",
        ["status", "locked_at", "updated_at"],
        unique=False,
    )
    if dialect == "postgresql":
        op.execute(
            "GRANT SELECT, INSERT ON TABLE "
            "public.notification_events, public.notification_recipients "
            "TO star_oam_api"
        )
        _replace_readiness(upgrade=True)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0107 supports PostgreSQL and SQLite only")
    if dialect == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute(
            "REVOKE INSERT ON TABLE "
            "public.notification_events, public.notification_recipients "
            "FROM star_oam_api"
        )
        _replace_readiness(upgrade=False)
    op.drop_index(
        "ix_notification_deliveries_dispatch",
        table_name="notification_deliveries",
    )
    op.drop_column("notification_deliveries", "locked_by")
    op.drop_column("notification_deliveries", "locked_at")
