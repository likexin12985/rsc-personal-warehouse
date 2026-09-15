"""Allow the API to record notification facts atomically with business facts.

Notification events and their recipient bindings are created by the same
transaction that records a shipment, receipt, inbound, or other business
fact.  The original material-request ACL made ``notification_events``
read-only and did not expose ``notification_recipients`` to the API, which
caused the first real shipment handover to fail with SQLSTATE 42501.  Keep
the grant narrow: the API can read and append these immutable notification
facts, but cannot update or delete them.
"""

from alembic import op


revision = "20261018_0108"
down_revision = "20261017_0107"
branch_labels = depends_on = None

API_ROLE = "star_oam_api"
TABLES = ("notification_events", "notification_recipients")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"GRANT SELECT, INSERT ON TABLE "
        f"{', '.join('public.' + name for name in TABLES)} TO {API_ROLE}"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"REVOKE INSERT ON TABLE "
        f"{', '.join('public.' + name for name in TABLES)} FROM {API_ROLE}"
    )
