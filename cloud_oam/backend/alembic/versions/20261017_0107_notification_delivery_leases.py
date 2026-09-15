"""Add explicit notification delivery leases.

The worker lease identifies the only process allowed to record a provider
result.  A stale ``sending`` row is intentionally not requeued automatically:
the provider may already have accepted the message, so replay would duplicate
an external notification.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261017_0107"
down_revision = "20261016_0106"
branch_labels = depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index(
        "ix_notification_deliveries_dispatch",
        table_name="notification_deliveries",
    )
    op.drop_column("notification_deliveries", "locked_by")
    op.drop_column("notification_deliveries", "locked_at")
